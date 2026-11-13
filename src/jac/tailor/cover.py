"""Cover note: LLM-written when available, always validated, with a fixed template fallback.

The model sees only facts from the fact bank plus the posting (as delimited data). Its note must
pass the validator. One retry is allowed; after that the note is built from facts by a template.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from jac.llm import UNTRUSTED_NOTICE, LLMClient, LLMError, parse_json_reply, wrap_untrusted
from jac.postings import Posting
from jac.profile import Profile
from jac.tailor.rank import Selection
from jac.tailor.validator import validate_text

log = logging.getLogger("jac.tailor")

COVER_SYSTEM = (
    "You write a short cover note (at most 150 words, plain text, no markdown) for a student applying to a job. "
    + UNTRUSTED_NOTICE
    + " Use ONLY the facts in the FACTS block. Do not add any number, percentage, year, company, tool or "
    'achievement that is not in FACTS or in the posting. Reply with JSON {"note": "..."} and nothing else.'
)


class CoverReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=40, max_length=1500)


@dataclass(frozen=True)
class CoverNote:
    text: str
    source: str  # "llm" or "template"
    attempts: int
    rejections: list[list[str]] = field(default_factory=list)


def _plain(text: str) -> str:
    return text.replace("**", "").replace("*", "")


def _top_bullets(profile: Profile, selection: Selection, limit: int) -> list[str]:
    work = ("experience", "project")  # citations and grades make poor cover-note material
    by_id = {b.id: b.text for f in profile.facts if f.kind in work for b in f.bullets}
    return [_plain(by_id[i]) for i in selection.priority if i in by_id][:limit]


def _facts_block(profile: Profile, selection: Selection) -> str:
    lines = [f"Name: {profile.identity.name}", f"Studies: {profile.constraints.degree_field}"]
    if profile.constraints.max_hours_per_week_in_term:
        lines.append(f"Availability in term: {profile.constraints.max_hours_per_week_in_term} hours per week")
    lines += [f"- {b}" for b in _top_bullets(profile, selection, 5)]
    return "\n".join(lines)


def template_note(profile: Profile, posting: Posting, selection: Selection, with_posting_data: bool = True) -> str:
    """Fixed template filled only from facts (and, optionally, the posting's title and company)."""
    at = f" at {posting.company}" if with_posting_data and posting.company else ""
    greeting = f"Dear hiring team{at},"
    role = f"the {posting.title} position" if with_posting_data else "this position"
    study = f"I am studying {profile.constraints.degree_field} and" if profile.constraints.degree_field else "I"
    lines = [greeting, "", f"{study} would like to apply for {role}. A few things from my CV that are relevant:", ""]
    lines += [f"- {b}" for b in _top_bullets(profile, selection, 3)]
    if profile.constraints.max_hours_per_week_in_term:
        lines += ["", f"During term I can work up to {profile.constraints.max_hours_per_week_in_term} hours per week."]
    lines += ["", "Kind regards,", profile.identity.name]
    return "\n".join(lines)


def _ask_llm(client: LLMClient, profile: Profile, posting: Posting, selection: Selection, feedback: list[str]) -> str:
    body = f"{posting.title} at {posting.company}\n{posting.description}"
    user = f"FACTS\n{_facts_block(profile, selection)}\n\nPOSTING\n{wrap_untrusted('POSTING', body)}"
    if feedback:
        user += "\n\nYour previous note was rejected for these reasons: " + "; ".join(feedback)
    reply = parse_json_reply(client.complete(COVER_SYSTEM, user), CoverReply)
    assert isinstance(reply, CoverReply)
    return reply.note.strip()


def write_cover_note(
    profile: Profile, posting: Posting, selection: Selection, llm: LLMClient | None = None
) -> CoverNote:
    """Return a validated cover note: the LLM's (one retry allowed) or the template."""
    rejections: list[list[str]] = []
    attempts = 0
    if llm is not None:
        feedback: list[str] = []
        for _ in range(2):
            attempts += 1
            try:
                text = _ask_llm(llm, profile, posting, selection, feedback)
            except LLMError as exc:
                feedback = [str(exc)]
                rejections.append(feedback)
                continue
            result = validate_text(text, profile, posting)
            if result.ok:
                return CoverNote(text, "llm", attempts, rejections)
            feedback = result.violations
            rejections.append(result.violations)
            log.warning("cover note rejected (attempt %d): %s", attempts, result.violations)
    text = template_note(profile, posting, selection)
    if not validate_text(text, profile, posting).ok:
        text = template_note(profile, posting, selection, with_posting_data=False)
    return CoverNote(text, "template", attempts, rejections)

