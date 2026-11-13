"""Combine the rules and the optional LLM extraction into one assessment."""
from __future__ import annotations

from dataclasses import dataclass, field

from jac.fit.llm_extract import Extraction, extract
from jac.fit.rules import Outcome, RuleResult, Verdict, run_rules, verdict_from
from jac.llm import LLMClient, LLMError
from jac.postings import Posting
from jac.profile import Profile


@dataclass(frozen=True)
class Assessment:
    verdict: Verdict
    results: list[RuleResult]
    llm_notes: list[str] = field(default_factory=list)

    def reasons(self) -> list[str]:
        """Human-readable lines for every rule that is not a plain pass, evidence included."""
        lines = []
        for r in self.results:
            if r.outcome is Outcome.PASS:
                continue
            lines.append(f"[{r.outcome.value}] {r.message}" + (f' "{r.evidence}"' if r.evidence else ""))
        return lines + [f"[llm] {n}" for n in self.llm_notes]


def _lower_by_llm(verdict: Verdict, extraction: Extraction, profile: Profile) -> tuple[Verdict, list[str]]:
    """The model may add notes and lower a fit to borderline. It never rejects or raises."""
    notes = list(extraction.notes)
    lowered = extraction.suggested_verdict != "fit"
    unspoken = {n.lower() for n in profile.constraints.languages_not_spoken}
    if any(lang.lower() in unspoken for lang in extraction.required_languages):
        notes.append("Model thinks a language you do not speak is required; please check.")
        lowered = True
    if lowered and verdict is Verdict.FIT:
        return Verdict.BORDERLINE, notes
    return verdict, notes


def assess(profile: Profile, posting: Posting, llm: LLMClient | None = None) -> Assessment:
    """Rules first; an optional LLM extraction can only add notes or lower fit to borderline."""
    results = run_rules(profile, posting)
    verdict = verdict_from(results)
    if llm is None:
        return Assessment(verdict, results)
    try:
        extraction = extract(llm, posting)
    except LLMError as exc:
        return Assessment(verdict, results, [f"LLM extraction skipped: {exc}"])
    verdict, notes = _lower_by_llm(verdict, extraction, profile)
    return Assessment(verdict, results, notes)
