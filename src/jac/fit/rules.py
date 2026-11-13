"""Explicit, testable fit rules. Each rule returns a RuleResult that quotes its evidence.

Rules decide first. Outcomes: PASS (fine), NOTE (shown, no effect), BORDERLINE (the user
decides) and REJECT. Citizenship and sponsorship lines are never auto-accepted or auto-rejected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum

from jac.fit.language import LANGUAGE_NAMES, detect_language
from jac.postings import Posting
from jac.profile import Profile
from jac.safety import find_injection


class Outcome(str, Enum):
    PASS = "pass"
    NOTE = "note"
    BORDERLINE = "borderline"
    REJECT = "reject"


class Verdict(str, Enum):
    FIT = "fit"
    BORDERLINE = "borderline"
    REJECT = "reject"
    MANUAL = "manual"


@dataclass(frozen=True)
class RuleResult:
    rule: str
    outcome: Outcome
    message: str
    evidence: str = ""


_LANGUAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "dutch": ("dutch", "nederlands", "nederlandse"),
    "german": ("german", "deutsch", "duits"),
    "french": ("french", "français", "francais", "frans"),
    "spanish": ("spanish", "español", "espanol", "spaans"),
    "italian": ("italian", "italiano", "italiaans"),
    "portuguese": ("portuguese", "português", "portugees"),
    "english": ("english", "engels"),
    "turkish": ("turkish", "türkçe", "turkce", "turks"),
}
_HARD_LANG = re.compile(
    r"\b(?:required|requires?|must|mandatory|essential|needed|fluent|fluency|native|proficien\w+|vloeiend\w*|"
    r"vereist|verplicht|excellent|good command|strong command|command of|beheers\w*|speak\w*|spreek\w*|spreekt)\b",
    re.IGNORECASE,
)
_SOFT = re.compile(
    r"\b(?:plus|advantage|nice[- ]to[- ]have|bonus|desirable|ideally|welcome|beneficial|preferred|preferably|"
    r"meerwaarde|voordeel|pré|not required|not necessary|no need|niet vereist|niet nodig|not a requirement|"
    r"is not mandatory)\b|een pre\b",
    re.IGNORECASE,
)
_LANG_WORDS = re.compile(r"\b(?:language|languages|taal|talen|speak\w*|spreek\w*|communicat\w*)\b", re.IGNORECASE)

_MASTER = re.compile(
    r"\b(?:master(?:['’]s)?\s+(?:degree|program(?:me)?|student|students|level|in|of|studies|thesis|opleiding)|"
    r"msc|m\.sc\.?)\b",
    re.IGNORECASE,
)
_PHD = re.compile(r"\b(?:phd|ph\.d\.?|doctorate|doctoral)\b", re.IGNORECASE)
_BACHELOR = re.compile(r"\b(?:bachelor|bsc|b\.sc|hbo|undergraduate)\w*", re.IGNORECASE)
_FINISHED_BACHELOR = re.compile(
    r"\b(?:completed|finished|graduated|obtained|holds?|have|recent graduate)\b.{0,40}\b(?:bachelor|bsc|degree)\b|"
    r"\b(?:bachelor|bsc)\w*(?:['’]s)?\s+degree\s+(?:is\s+)?required\b",
    re.IGNORECASE,
)
_YEARS = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*)?(?:years?|yrs|jaar)\b", re.IGNORECASE)
_HOURS = re.compile(
    r"(\d{1,2})(?:\s*(?:-|–|to|tot)\s*(\d{1,2}))?\s*(?:hours?|hrs?|h|uur|uren)\s*(?:per|a|/|each|p\.?)\s*(?:week|wk|w)\b",
    re.IGNORECASE,
)
_DAYS = re.compile(r"(\d)(?:\s*(?:-|–|to)\s*\d)?\s*days?\s*(?:per|a|/)\s*week\b", re.IGNORECASE)
_FULL_TIME = re.compile(r"\bfull[- ]?time\b", re.IGNORECASE)
_FLEX = re.compile(r"\b(?:part[- ]?time|flexible hours|hours per week|hours a week)\b", re.IGNORECASE)
_VACATION = re.compile(r"\b(?:summer|zomer\w*|vacation|semester break|between semesters|after graduation)\b", re.IGNORECASE)
_FULL_REMOTE = re.compile(
    r"\b(?:fully remote|100% remote|remote[- ]first|remote (?:position|role|job|work)|work remotely|location: remote)\b",
    re.IGNORECASE,
)
_SPONSOR = re.compile(
    r"sponsor|work permit|right to work|eligible to work|work authori[sz]ation|citizen|nationality|security clearance|"
    r"\bvisa\b|residen(?:t|cy) (?:permit|status)|\b(?:eu|eea) national",
    re.IGNORECASE,
)
_START = re.compile(
    r"(?:start(?:ing|s)?(?: date)?|from|begin(?:ning)?)\s*(?:in|on|at)?\s*"
    r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d\d)",
    re.IGNORECASE,
)
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_LEVELS = {"none": 0, "bachelor": 1, "master": 2, "phd": 3}


def sentences(text: str) -> list[str]:
    """Split posting text into sentence-like units, one per bullet or sentence."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" •-\t") for p in parts if len(p.strip(" •-\t")) > 2]


def _quote(sentence: str) -> str:
    return sentence if len(sentence) <= 240 else sentence[:237] + "..."


def _holds_level(profile: Profile, on: date | None) -> int:
    """Highest degree level the user holds on a given date."""
    c = profile.constraints
    level = _LEVELS[c.degree_level]
    if c.degree_in_progress and (c.graduation is None or on is None or c.graduation > on):
        return level - 1
    return level


def _enrolled_level(profile: Profile) -> int:
    return _LEVELS[profile.constraints.degree_level]


def _unspoken(profile: Profile) -> set[str]:
    """Languages the user lacks: the explicit list plus every known language not in their spoken list."""
    spoken = profile.constraints.spoken_names()
    explicit = {n.lower() for n in profile.constraints.languages_not_spoken}
    return explicit | ((set(_LANGUAGE_ALIASES) - spoken) if spoken else set())


def rule_posting_language(profile: Profile, posting: Posting) -> RuleResult:
    """Reject a posting whose own text is in a language the user does not speak."""
    code = detect_language(posting.description)
    if code == "unknown":
        return RuleResult("posting_language", Outcome.NOTE, "Could not tell which language the posting is written in.")
    name = LANGUAGE_NAMES[code]
    if name.lower() in _unspoken(profile):
        return RuleResult("posting_language", Outcome.REJECT, f"The posting is written in {name}, which you do not speak.")
    return RuleResult("posting_language", Outcome.PASS, f"The posting is written in {name}.")


def _clause_with(sentence: str, aliases: tuple[str, ...]) -> str:
    """The clause (split at ';' and 'but'/'while'/'whereas') that names the language.

    A soft word must sit in the same clause to soften it: in "Fluent Dutch is required;
    English is a plus" the "plus" belongs to English, not to Dutch. Commas are not split,
    because "Fluency in Dutch, German or French is a plus" is one soft clause.
    """
    clauses = re.split(r";|\b(?:but|while|whereas)\b", sentence, flags=re.IGNORECASE)
    for clause in clauses:
        if any(re.search(rf"\b{re.escape(a)}\b", clause.lower()) for a in aliases):
            return clause
    return sentence


def rule_required_languages(profile: Profile, posting: Posting) -> RuleResult:
    """Reject when a language the user does not speak is required; notes for soft mentions."""
    unspoken = _unspoken(profile)
    notes: list[RuleResult] = []
    for sentence in sentences(posting.title + "\n" + posting.description):
        lowered = sentence.lower()
        for language in sorted(unspoken):
            aliases = _LANGUAGE_ALIASES.get(language, (language,))
            if not any(re.search(rf"\b{re.escape(a)}\b", lowered) for a in aliases):
                continue
            label = language.capitalize()
            if _SOFT.search(_clause_with(sentence, aliases)):
                notes.append(RuleResult("required_languages", Outcome.NOTE, f"{label} is mentioned as a plus, not a requirement.", _quote(sentence)))
            elif _HARD_LANG.search(sentence):
                return RuleResult("required_languages", Outcome.REJECT, f"{label} is required and you do not speak it.", _quote(sentence))
            elif _LANG_WORDS.search(sentence):
                notes.append(RuleResult("required_languages", Outcome.NOTE, f"{label} is mentioned; check how important it is.", _quote(sentence)))
    return notes[0] if notes else RuleResult("required_languages", Outcome.PASS, "No language you lack is required.")


def rule_degree(profile: Profile, posting: Posting) -> RuleResult:
    """Master's/PhD requirements reject; a finished bachelor's before start is borderline."""
    c = profile.constraints
    enrolled = _enrolled_level(profile)
    for sentence in sentences(posting.title + "\n" + posting.description):
        if _SOFT.search(sentence):
            continue
        wants_phd, wants_master = bool(_PHD.search(sentence)), bool(_MASTER.search(sentence))
        if (wants_phd or wants_master) and not _BACHELOR.search(sentence):
            needed = 3 if wants_phd else 2
            if enrolled < needed:
                label = "PhD" if wants_phd else "master's"
                return RuleResult("degree", Outcome.REJECT, f"The posting needs {label} enrolment or a finished {label} degree; you are on a {c.degree_level} level.", _quote(sentence))
        elif _FINISHED_BACHELOR.search(sentence) and _holds_level(profile, c.earliest_start) < 1:
            when = f" (you graduate {c.graduation})" if c.graduation else ""
            return RuleResult("degree", Outcome.BORDERLINE, f"The posting may need a finished bachelor's before start{when}.", _quote(sentence))
    return RuleResult("degree", Outcome.PASS, "No degree requirement above your level was found.")


def rule_experience(profile: Profile, posting: Posting) -> RuleResult:
    """Compare the stated years of experience with the user's own count."""
    have = profile.constraints.years_experience
    worst: tuple[int, str] | None = None
    for sentence in sentences(posting.description):
        if not re.search(r"experience|ervaring", sentence, re.IGNORECASE):
            continue
        if re.match(r"^\W*(?:we|our|with over|for over|since)\b", sentence, re.IGNORECASE) and not re.search(r"\byou", sentence, re.IGNORECASE):
            continue
        match = _YEARS.search(sentence)
        if not match or _SOFT.search(sentence):
            continue
        years = int(match.group(1))
        if worst is None or years > worst[0]:
            worst = (years, sentence)
    if worst is None:
        return RuleResult("experience", Outcome.PASS, "No years-of-experience requirement found.")
    years, sentence = worst
    gap = years - have
    if gap >= 2:
        return RuleResult("experience", Outcome.REJECT, f"Asks for {years}+ years of experience; you have {have:g}.", _quote(sentence))
    if gap > 0:
        return RuleResult("experience", Outcome.BORDERLINE, f"Asks for {years} years of experience; you have {have:g}.", _quote(sentence))
    return RuleResult("experience", Outcome.PASS, f"Asks for {years} years; you have {have:g}.", _quote(sentence))


def _explicit_hours(text: str) -> tuple[int, str] | None:
    for sentence in sentences(text):
        if match := _HOURS.search(sentence):
            return int(match.group(1)), sentence
        if match := _DAYS.search(sentence):
            return int(match.group(1)) * 8, sentence
    return None


def rule_hours(profile: Profile, posting: Posting) -> RuleResult:
    """Compare weekly hours with the user's term-time maximum."""
    limit = profile.constraints.max_hours_per_week_in_term
    if limit is None:
        return RuleResult("hours", Outcome.PASS, "No weekly-hours limit set in your profile.")
    text = posting.description
    explicit = _explicit_hours(text)
    if explicit and explicit[0] > limit:
        hours, sentence = explicit
        if _VACATION.search(text):
            return RuleResult("hours", Outcome.BORDERLINE, f"{hours} hours per week, above your {limit}; it may be outside term time.", _quote(sentence))
        return RuleResult("hours", Outcome.REJECT, f"{hours} hours per week is above your term-time maximum of {limit}.", _quote(sentence))
    if explicit:
        return RuleResult("hours", Outcome.PASS, f"{explicit[0]} hours per week fits your maximum of {limit}.", _quote(explicit[1]))
    full_time = _FULL_TIME.search(text) or "FULL_TIME" in posting.employment_type.upper()
    if full_time and not _FLEX.search(text):
        return RuleResult("hours", Outcome.BORDERLINE, f"Listed as full-time and no hours are given; your maximum in term is {limit}.")
    return RuleResult("hours", Outcome.PASS, "No hours conflict found.")


def rule_period(profile: Profile, posting: Posting) -> RuleResult:
    """Check an explicit start month against the user's earliest start; note durations."""
    earliest = profile.constraints.earliest_start
    for sentence in sentences(posting.description):
        match = _START.search(sentence)
        if match and earliest:
            start = date(int(match.group(2)), _MONTHS.index(match.group(1).lower()) + 1, 1)
            if start < date(earliest.year, earliest.month, 1):
                return RuleResult("period", Outcome.BORDERLINE, f"Starts {match.group(1).title()} {match.group(2)}, before your earliest start {earliest}.", _quote(sentence))
        if duration := re.search(r"(\d{1,2})\s*(?:-\s*\d{1,2}\s*)?(months?|weeks?)\b", sentence, re.IGNORECASE):
            return RuleResult("period", Outcome.NOTE, f"Duration mentioned: {duration.group(0)}.", _quote(sentence))
    return RuleResult("period", Outcome.PASS, "No period conflict found.")


def rule_location(profile: Profile, posting: Posting) -> RuleResult:
    """Match the posting location against the user's locations or a fully remote role."""
    c = profile.constraints
    if not c.locations:
        return RuleResult("location", Outcome.PASS, "No location preference set.")
    location = posting.location.lower()
    if any(term.lower() in location for term in c.locations):
        return RuleResult("location", Outcome.PASS, f"Location {posting.location!r} matches your preferences.")
    remote = _FULL_REMOTE.search(posting.location + "\n" + posting.description) or "remote" in location
    if remote and c.remote_ok:
        return RuleResult("location", Outcome.NOTE, "Remote role; the office location is outside your list.")
    if not location:
        return RuleResult("location", Outcome.NOTE, "The posting gives no location.")
    return RuleResult("location", Outcome.REJECT, f"Location {posting.location!r} is outside your locations and the role is not fully remote.")


def rule_authorisation(profile: Profile, posting: Posting) -> RuleResult:
    """Quote citizenship and sponsorship lines. The user decides: never auto-accept or reject."""
    hits = [s for s in sentences(posting.description) if _SPONSOR.search(s)]
    if not hits:
        return RuleResult("authorisation", Outcome.PASS, "No citizenship or sponsorship line found.")
    note = f" Your notes: {profile.constraints.work_authorisation_notes}" if profile.constraints.work_authorisation_notes else ""
    return RuleResult("authorisation", Outcome.BORDERLINE, f"The posting has a work-authorisation line; you decide.{note}", _quote(" / ".join(hits[:2])))


def rule_role(profile: Profile, posting: Posting) -> RuleResult:
    """Note (never reject) when the title shares no word with the roles of interest."""
    roles = profile.constraints.roles_of_interest
    if not roles:
        return RuleResult("role", Outcome.PASS, "No roles of interest set.")
    words = {w for role in roles for w in re.findall(r"[a-z]{4,}", role.lower())}
    if words & set(re.findall(r"[a-z]{4,}", posting.title.lower())):
        return RuleResult("role", Outcome.PASS, "The title matches your roles of interest.")
    return RuleResult("role", Outcome.NOTE, "The title does not match your roles of interest.")


def rule_injection(profile: Profile, posting: Posting) -> RuleResult:
    """Text aimed at AI readers is ignored as data, and lowers the verdict to borderline."""
    hits = find_injection(posting.title + "\n" + posting.description)
    if not hits:
        return RuleResult("injection", Outcome.PASS, "No text aimed at AI readers found.")
    return RuleResult("injection", Outcome.BORDERLINE, "The posting contains text aimed at AI readers; it was treated as data and ignored.", _quote(hits[0]))


RULES = (
    rule_injection,
    rule_posting_language,
    rule_required_languages,
    rule_degree,
    rule_experience,
    rule_hours,
    rule_period,
    rule_location,
    rule_authorisation,
    rule_role,
)


def run_rules(profile: Profile, posting: Posting) -> list[RuleResult]:
    """Run every rule in a fixed order."""
    return [rule(profile, posting) for rule in RULES]


def verdict_from(results: list[RuleResult]) -> Verdict:
    """Worst outcome wins: any REJECT rejects, any BORDERLINE is borderline, else fit."""
    outcomes = {r.outcome for r in results}
    if Outcome.REJECT in outcomes:
        return Verdict.REJECT
    if Outcome.BORDERLINE in outcomes:
        return Verdict.BORDERLINE
    return Verdict.FIT
