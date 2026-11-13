"""Validator for generated text (the cover note).

Rejects a text when it contains a number, percentage, year, company, technology or name that
is not in the fact bank or in the posting's title, company or location, or any never_claim
string. The posting description is deliberately NOT a source of allowed claims: "5 years of
Docker at Initech" in a posting must not turn into a claim about the user. Invisible, look-alike
and markup characters are rejected too, so a claim cannot hide behind them.
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

from jac.postings import Posting
from jac.profile import Profile, find_never_claim

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-/]*[A-Za-z0-9+#]|[A-Za-z]")
_MARKUP = re.compile(r"&#?\w+;|<[A-Za-z/!]")
_NUMBER_WORDS = frozenset(
    "two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred hundreds thousand "
    "thousands million millions billion dozen dozens twice".split()
)
# Ordinary words that may start a sentence. Any other capitalised word at a sentence start is
# treated like a name, so "Globex hired me" cannot slip through as a sentence opener.
_COMMON = frozenset(
    "a about above after again against all also although always among an and another any are as at "
    "because been before being besides beyond both but by can could currently dear during each either even "
    "ever every finally first for from further furthermore given having he her here hers him his how however "
    "i if in including into is it its just kind last lately let many may me meanwhile might more moreover most "
    "much my next no nor not now of on once one only or other otherwise our over overall perhaps please "
    "previously rather recently regards since sincerely so some still such than thank thanks that the their "
    "them then there therefore these they this those though through throughout thus to today together too "
    "under unlike until upon very was we were what when where whether which while who whom whose why will "
    "with within without would yes yet you your yours best warm hello hi additionally alongside especially "
    "outside working having studying building learning looking applying combining based".split()
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
    capitalised = word[0].isupper() and (not sentence_start or word.lower() not in _COMMON)
    return bool(inner_capital or odd_chars or capitalised)


def _odd_characters(text: str, trusted: str) -> list[str]:
    """Invisible, compatibility (superscript, full-width) and non-Latin characters, and markup.

    A character that already occurs in trusted text (the bank, the posting's title, company or
    location) is fine; anything else of these kinds can hide a claim from the checks below.
    """
    found: set[str] = set()
    for ch in set(text) - set(trusted):
        if unicodedata.category(ch) == "Cf":
            found.add("invisible formatting character")
        elif unicodedata.normalize("NFKC", ch) != ch:
            found.add("compatibility character (superscript, full-width or similar)")
        elif ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN"):
            found.add("letter from a non-Latin script")
    if _MARKUP.search(text):
        found.add("HTML markup or entity")
    return [f"text contains a {kind}" for kind in sorted(found)]


def _folded(text: str) -> str:
    """Text as a reader would see it: entities decoded, compatibility forms folded, invisibles dropped."""
    text = unicodedata.normalize("NFKC", html.unescape(text))
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def validate_text(text: str, profile: Profile, posting: Posting) -> ValidationResult:
    """Check a generated text against the fact bank, the posting's names and never_claim."""
    text = text.replace("**", "")
    posting_text = f"{posting.title}\n{posting.company}\n{posting.location}"
    bank = profile.bank_text()
    violations = _odd_characters(text, bank + posting_text)

    for term in sorted(find_never_claim(text, profile.never_claim) | find_never_claim(_folded(text), profile.never_claim)):
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
            violations.append(f"{kind} not in the fact bank or the posting's title, company or location: {word}")
    return ValidationResult(violations)
