"""`jac init`: ask plain questions with sensible defaults, write config.yaml and profile.yaml.

The same questions can be answered from a YAML file (--answers) for scripts and tests.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from jac.config import AUTHSERV_PRESETS, IMAP_PRESETS, Config, ConfigError, dump_config
from jac.paths import Locations
from jac.profile import Constraints, Identity, Language, Profile, dump_profile


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    default: str = ""
    choices: tuple[str, ...] = ()


QUESTIONS: tuple[Question, ...] = (
    Question("name", "Your full name"),
    Question("email", "Your e-mail address (shown on the CV)"),
    Question("headline", "One line about you for the CV header, e.g. 'BSc Computer Science student'"),
    Question("linkedin", "LinkedIn profile URL (optional)"),
    Question("github", "GitHub URL (optional)"),
    Question("languages_spoken", "Languages you speak, as name:level comma separated", "English:fluent"),
    Question("languages_not_spoken", "Languages you do NOT speak (postings requiring them are rejected)"),
    Question("degree_level", "Degree you are studying for or hold", "bachelor", ("none", "bachelor", "master", "phd")),
    Question("degree_field", "Field of study, e.g. Computer Science"),
    Question("degree_in_progress", "Are you still studying for it? (yes/no)", "yes"),
    Question("graduation", "Graduation date, YYYY-MM-DD (optional)"),
    Question("earliest_start", "Earliest date you can start, YYYY-MM-DD (optional)"),
    Question("max_hours", "Maximum hours per week while studying (empty for no limit)"),
    Question("locations", "Locations you accept, comma separated (empty for anywhere)"),
    Question("remote_ok", "Is remote work ok? (yes/no)", "yes"),
    Question("roles", "Kinds of roles you want, comma separated, e.g. 'data science intern, software engineer'"),
    Question("never_claim", "Words that must NEVER appear in your CV or notes (tools or employers you have not used)"),
    Question("work_notes", "Visa or work-permit situation in your own words (shown next to such lines)"),
    Question("mail_method", "How should the tool read your alert mails?", "eml", ("eml", "imap", "gmail")),
    Question("mail_provider", "Mail provider", "gmail", tuple(AUTHSERV_PRESETS)),
    Question("mail_user", "Mail login (IMAP only; the password is asked later and kept in the OS keyring)"),
)
_YES = {"y", "yes", "true", "1", "ja", "evet", "e"}


def _listing(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value or "").split(",") if p.strip()]


def _flag(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in _YES


def _day(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ConfigError(f"{value!r} is not a date; use YYYY-MM-DD") from exc


def _languages(value: Any) -> list[Language]:
    out = []
    for item in _listing(value):
        name, _, level = item.partition(":")
        out.append(Language(name=name.strip().title(), level=level.strip() or "fluent"))
    return out


def build(answers: dict[str, Any], mail_folder: str = "mail") -> tuple[Config, Profile]:
    """Turn raw answers (strings or YAML values) into a validated Config and Profile."""
    a = {q.key: answers.get(q.key, q.default) for q in QUESTIONS}
    for q in QUESTIONS:
        if q.choices and str(a[q.key]) not in q.choices:
            raise ConfigError(f"{q.key} must be one of {', '.join(q.choices)} (got {a[q.key]!r})")
    links = {k: str(a[k]) for k in ("linkedin", "github") if a[k]}
    hours = str(a["max_hours"]).strip()
    profile = Profile(
        identity=Identity(name=str(a["name"]), email=str(a["email"]), headline=str(a["headline"]), links=links),
        constraints=Constraints(
            languages=_languages(a["languages_spoken"]),
            languages_not_spoken=[x.title() for x in _listing(a["languages_not_spoken"])],
            degree_level=a["degree_level"],
            degree_field=str(a["degree_field"]),
            degree_in_progress=_flag(a["degree_in_progress"]),
            graduation=_day(a["graduation"]),
            earliest_start=_day(a["earliest_start"]),
            max_hours_per_week_in_term=int(hours) if hours else None,
            locations=_listing(a["locations"]),
            remote_ok=_flag(a["remote_ok"]),
            work_authorisation_notes=str(a["work_notes"]),
            roles_of_interest=_listing(a["roles"]),
        ),
        never_claim=_listing(a["never_claim"]),
    )
    cfg = Config()
    cfg.mail.method = a["mail_method"]
    cfg.mail.provider = str(a["mail_provider"])
    cfg.mail.eml_folder = mail_folder
    if a["mail_method"] == "imap":
        host, port = IMAP_PRESETS[cfg.mail.provider]
        cfg.mail.imap.host, cfg.mail.imap.port, cfg.mail.imap.user = host, port, str(a["mail_user"])
        if not a["mail_user"]:
            raise ConfigError("mail_user is required for IMAP")
    return cfg, profile


def ask_questions(ask: Callable[[str], str]) -> dict[str, Any]:
    """Interactive loop: show the default, accept empty input, re-ask on a bad choice."""
    answers: dict[str, Any] = {}
    for q in QUESTIONS:
        hint = f" [{q.default}]" if q.default else ""
        options = f" ({'/'.join(q.choices)})" if q.choices else ""
        while True:
            reply = ask(f"{q.prompt}{options}{hint}: ").strip()
            reply = reply or q.default
            if not q.choices or reply in q.choices:
                answers[q.key] = reply
                break
            print(f"Please choose one of: {', '.join(q.choices)}")
    return answers


def run_wizard(loc: Locations, answers_file: Path | None = None, ask: Callable[[str], str] = input,
               force: bool = False) -> tuple[Path, Path]:
    """Write config.yaml and profile.yaml into the config directory and return their paths."""
    if loc.profile_file.exists() and not force:
        raise ConfigError(f"{loc.profile_file} already exists; use --force to overwrite it")
    if answers_file:
        answers = yaml.safe_load(answers_file.read_text(encoding="utf-8")) or {}
        unknown = set(answers) - {q.key for q in QUESTIONS}
        if unknown:
            raise ConfigError(f"unknown answers: {', '.join(sorted(unknown))}")
    else:
        answers = ask_questions(ask)
    cfg, profile = build(answers)
    loc.config_dir.mkdir(parents=True, exist_ok=True)
    (loc.config_dir / cfg.mail.eml_folder).mkdir(exist_ok=True)
    dump_config(cfg, loc.config_file)
    dump_profile(profile, loc.profile_file, header="# Created by `jac init`. Add facts with `jac import-cv`.\n")
    return loc.config_file, loc.profile_file
