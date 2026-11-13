"""Optional structured extraction from a posting through a local LLM.

The model reads the posting as data and returns a strict JSON object. Its output can add
notes or lower a verdict (never below 'borderline'); it can never raise a verdict that the
rules lowered.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jac.llm import UNTRUSTED_NOTICE, LLMClient, parse_json_reply, wrap_untrusted
from jac.postings import Posting

SYSTEM_PROMPT = (
    "You extract requirements from a job posting for a student. " + UNTRUSTED_NOTICE + " "
    'Return one JSON object with exactly these keys: "required_languages" (list of language names the '
    'posting requires), "min_years_experience" (number or null), "requires_degree" ("none", "bachelor", '
    '"master" or "phd"), "hours_per_week_min" (integer or null), "remote" ("yes", "no", "hybrid" or '
    '"unknown"), "suggested_verdict" ("fit", "borderline" or "reject") and "notes" (at most 5 short strings).'
)


class Extraction(BaseModel):
    """Strict schema for the model's reply. Unknown keys or wrong types are rejected."""

    model_config = ConfigDict(extra="forbid")

    required_languages: list[str] = Field(max_length=10)
    min_years_experience: float | None = None
    requires_degree: Literal["none", "bachelor", "master", "phd"]
    hours_per_week_min: int | None = None
    remote: Literal["yes", "no", "hybrid", "unknown"]
    suggested_verdict: Literal["fit", "borderline", "reject"]
    notes: list[str] = Field(default_factory=list, max_length=5)


def build_prompt(posting: Posting) -> tuple[str, str]:
    """System and user message; the posting is wrapped as untrusted data."""
    body = f"Title: {posting.title}\nCompany: {posting.company}\nLocation: {posting.location}\n\n{posting.description}"
    return SYSTEM_PROMPT, "Extract the requirements from this posting.\n" + wrap_untrusted("POSTING", body)


def extract(client: LLMClient, posting: Posting) -> Extraction:
    """Ask the model for an Extraction. Raises LLMError when the reply is unusable."""
    system, user = build_prompt(posting)
    result = parse_json_reply(client.complete(system, user), Extraction)
    assert isinstance(result, Extraction)
    return result
