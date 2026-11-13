"""Profile model: who the user is, what they will and will not do, and the fact bank.

The fact bank is the only source of claims about the user. Everything the tool writes
(CV, cover note) must be traceable to a fact id or be rejected by the validator.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FactKind = Literal["experience", "project", "publication", "education"]
DegreeLevel = Literal["none", "bachelor", "master", "phd"]

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class _Strict(BaseModel):
    """Base model that rejects unknown keys, so typos in profile.yaml fail loudly."""

    model_config = ConfigDict(extra="forbid")


class Identity(_Strict):
    name: str = ""
    headline: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: dict[str, str] = Field(default_factory=dict)


class Language(_Strict):
    name: str
    level: str


class Constraints(_Strict):
    """Hard preferences the fit rules check against. Every field has a neutral default."""

    languages: list[Language] = Field(default_factory=list)
    languages_not_spoken: list[str] = Field(default_factory=list)
    degree_level: DegreeLevel = "none"
    degree_field: str = ""
    degree_in_progress: bool = False
    graduation: date | None = None
    earliest_start: date | None = None
    years_experience: float = 0.0
    max_hours_per_week_in_term: int | None = None
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool = True
    work_authorisation_notes: str = ""
    roles_of_interest: list[str] = Field(default_factory=list)

    def spoken_names(self) -> set[str]:
        return {lang.name.strip().lower() for lang in self.languages}


class Bullet(_Strict):
    id: str
    text: str

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(f"bullet id {value!r} must be lowercase letters, digits, - _ .")
        return value


class Fact(_Strict):
    """One experience, project, publication or education entry with stable ids."""

    id: str
    kind: FactKind
    title: str
    org: str = ""
    start: str = ""
    end: str = ""
    dates: str = ""
    summary: str = ""
    link: str = ""
    team: str = ""
    tags: list[str] = Field(default_factory=list)
    bullets: list[Bullet] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(f"fact id {value!r} must be lowercase letters, digits, - _ .")
        return value

    def texts(self) -> list[str]:
        """Every free-text field of this fact, used to build the allowed-claims vocabulary."""
        parts = [self.title, self.org, self.start, self.end, self.dates, self.summary, self.team]
        parts += self.tags
        parts += [b.text for b in self.bullets]
        return [p for p in parts if p]


class SkillGroup(_Strict):
    name: str
    items: list[str]


class Profile(_Strict):
    identity: Identity = Field(default_factory=Identity)
    constraints: Constraints = Field(default_factory=Constraints)
    summary: str = ""
    facts: list[Fact] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    never_claim: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_ids_and_never_claim(self) -> "Profile":
        seen: set[str] = set()
        for fact in self.facts:
            for ident in [fact.id, *[b.id for b in fact.bullets]]:
                if ident in seen:
                    raise ValueError(f"duplicate id in fact bank: {ident}")
                seen.add(ident)
        hits = find_never_claim(self.bank_text(), self.never_claim)
        if hits:
            raise ValueError(f"fact bank contains never_claim strings: {sorted(hits)}")
        return self

    def fact_by_id(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    def bank_text(self) -> str:
        """All text the user has vouched for. The validator treats it as the allowed vocabulary."""
        parts = [
            self.identity.name,
            self.identity.headline,
            self.identity.location,
            self.summary,
            self.constraints.degree_field,
        ]
        parts += [str(self.constraints.graduation or ""), str(self.constraints.earliest_start or "")]
        if self.constraints.max_hours_per_week_in_term:
            parts.append(f"{self.constraints.max_hours_per_week_in_term} hours per week")
        for fact in self.facts:
            parts += fact.texts()
        for group in self.skills:
            parts += [group.name, *group.items]
        parts += [lang.name for lang in self.constraints.languages]
        return "\n".join(p for p in parts if p).replace("**", "")


def never_claim_pattern(term: str) -> re.Pattern[str]:
    """Case-insensitive match that does not fire inside a longer alphanumeric word."""
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.IGNORECASE)


def find_never_claim(text: str, terms: list[str]) -> set[str]:
    """Return the never_claim terms that appear in text."""
    return {t for t in terms if t and never_claim_pattern(t).search(text)}


def load_profile(path: Path) -> Profile:
    """Load and validate profile.yaml."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Profile.model_validate(data)


def dump_profile(profile: Profile, path: Path, header: str = "") -> None:
    """Write a profile as YAML (UTF-8, keys in model order)."""
    data = profile.model_dump(mode="json", exclude_defaults=False)
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
    path.write_text(header + body, encoding="utf-8")
