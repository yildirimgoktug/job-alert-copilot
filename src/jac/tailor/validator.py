"""Validator for generated text (the cover note).

Rejects a text when it contains a number, percentage, year, company, technology or name that
appears neither in the fact bank nor in the posting, or any never_claim string. This is the
rule that keeps generated text from inventing experience.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from jac.postings import Posting
from jac.profile import Profile, find_never_claim

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-/]*[A-Za-z0-9+#]|[A-Za-z]")
_NUMBER_WORDS = frozenset(
    "two three four five six seven eight nine ten eleven twelve twenty thirty forty fifty hundred thousand "
    "million dozen".split()
)
_TECH_TERMS = frozenset(
    "python java javascript typescript golang rust kotlin swift scala ruby php sql nosql docker kubernetes "
    "terraform aws azure gcp spark hadoop kafka airflow tensorflow keras pytorch jax opencv numpy pandas "
    "react angular vue django flask fastapi spring node.js postgres postgresql mysql mongodb redis git linux "
    "tableau powerbi snowflake databricks llm rag bert gpt cuda ros matlab c++ c# scikit-learn".split()
)
_GENERIC = frozenset(
    "i dear hello hi hiring team manager regards sincerely kind best thank thanks yours cv ai ml".split()
)


@dataclass(frozen=True)
class ValidationResult:
    violations: list[str]

    @property
    def ok(self) -> bool:
        return not self.violations


def normalise_number(raw: str) -> str:
    """'6,100' -> '6100', '09' -> '9', '5.70' stays comparable as text."""
    value = raw.replace(",", "").rstrip(".")
    return str(int(value)) if value.isdigit() else value


def numbers_in(text: str) -> set[str]:
    return {normalise_number(m.group(0)) for m in _NUMBER.finditer(text)}


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower().rstrip(".") for m in _WORD.finditer(text)}


def _is_sentence_start(text: str, index: int) -> bool:
    before = text[:index].rstrip(" \t*-•")
    return not before or before[-1] in ".!?:\n"


def _looks_like_a_name(word: str, sentence_start: bool) -> bool:
    inner_capital = any(c.isupper() for c in word[1:])
    odd_chars = any(c in word for c in "+#") or re.search(r"[a-z]\.[a-z]", word) or re.search(r"[A-Za-z]\d", word)
    return bool(inner_capital or odd_chars or (word[0].isupper() and not sentence_start))


def validate_text(text: str, profile: Profile, posting: Posting) -> ValidationResult:
    """Check a generated text against the fact bank, the posting and never_claim."""
    text = text.replace("**", "")
    posting_text = f"{posting.title}\n{posting.company}\n{posting.location}\n{posting.description}\n{posting.date_posted}"
    bank = profile.bank_text()
    violations: list[str] = []

    for term in sorted(find_never_claim(text, profile.never_claim)):
        violations.append(f"never_claim string present: {term}")

    allowed_numbers = numbers_in(bank) | numbers_in(posting_text)
    for number in sorted(numbers_in(text) - allowed_numbers):
        violations.append(f"number not in the fact bank or posting: {number}")

    allowed_words = _tokens(bank) | _tokens(posting_text) | _GENERIC
    for word in sorted(_tokens(text) & _NUMBER_WORDS - allowed_words):
        violations.append(f"number word not in the fact bank or posting: {word}")

    seen: set[str] = set()
    for match in _WORD.finditer(text):
        word = match.group(0).rstrip(".")
        lowered = word.lower()
        if lowered in seen or lowered in allowed_words:
            continue
        is_tech = lowered in _TECH_TERMS
        if is_tech or _looks_like_a_name(word, _is_sentence_start(text, match.start())):
            seen.add(lowered)
            kind = "technology" if is_tech else "name"
            violations.append(f"{kind} not in the fact bank or posting: {word}")
    return ValidationResult(violations)
