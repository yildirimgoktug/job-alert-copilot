"""Read a PDF CV into a draft profile: pypdf text extraction plus simple heading/bullet heuristics.

PDFs carry no structure, so this is a best effort. The draft always needs the user's review;
`jac profile check` shows what was understood and what is missing.
"""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

from jac.importers.draft import IdFactory, make_fact, split_sentences
from jac.profile import Fact, Identity, Profile, SkillGroup

_HEADINGS = {
    "summary": ("profile", "summary", "about", "about me", "objective"),
    "education": ("education", "studies"),
    "experience": ("experience", "work experience", "employment", "work history"),
    "project": ("projects", "ai projects", "selected projects", "personal projects"),
    "publication": ("publications", "papers"),
    "skills": ("skills", "technical skills", "languages"),
}
_BULLET = re.compile(r"^\s*[•\-–*·▪◦]\s+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d \-()]{7,}\d")
_URL = re.compile(r"(?:https?://)?(?:www\.)?(?:linkedin\.com/in|github\.com)/[\w.-]+", re.I)
_DATES = re.compile(
    r"((?:[A-Z][a-z]{2,8}\.? )?(?:19|20)\d\d\s*[–-]\s*(?:(?:[A-Z][a-z]{2,8}\.? )?(?:19|20)\d\d|Present|present|Now)[^\n]*)"
)


def pdf_lines(path: Path) -> list[str]:
    """All non-empty text lines of the PDF, in reading order."""
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _heading_of(line: str) -> str | None:
    key = line.strip(" :").lower()
    return next((section for section, names in _HEADINGS.items() if key in names), None)


def _identity(lines: list[str]) -> Identity:
    head = " ".join(lines[:6])
    links: dict[str, str] = {}
    for match in _URL.findall(head):
        url = match if match.startswith("http") else "https://" + match
        links["github" if "github" in url else "linkedin"] = url
    email, phone = _EMAIL.search(head), _PHONE.search(head)
    return Identity(
        name=lines[0] if lines else "",
        email=email.group(0) if email else "",
        phone=phone.group(0).strip() if phone else "",
        links=links,
    )


def _entries(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Group lines into (title line, bullet texts). A non-bullet line after bullets starts a new entry."""
    entries: list[tuple[str, list[str]]] = []
    title, bullets, after_bullet = "", [], False
    for line in lines:
        if _BULLET.match(line):
            bullets.append(_BULLET.sub("", line))
            after_bullet = True
        elif after_bullet:
            if line[:1].islower():
                bullets[-1] += " " + line
            else:
                entries.append((title, bullets))
                title, bullets, after_bullet = line, [], False
        elif title and not bullets and line[:1].islower():
            title += " " + line
        elif title and not bullets:
            bullets.append(line)
        else:
            if title:
                entries.append((title, bullets))
                bullets = []
            title = line
    if title:
        entries.append((title, bullets))
    return entries


def parse_cv_lines(lines: list[str]) -> Profile:
    """Heuristic parse of CV text lines into a draft Profile."""
    ids = IdFactory()
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in lines:
        section = _heading_of(line)
        if section:
            current = section
            sections.setdefault(current, [])
        else:
            sections[current].append(line)
    facts: list[Fact] = []
    for kind in ("education", "experience", "project", "publication"):
        for title, bullets in _entries(sections.get(kind, [])):
            found = _DATES.search(title)
            dates = found.group(1).strip() if found else ""
            clean = title.replace(dates, "").strip(" ,·|-–") if dates else title
            if kind == "project" and len(bullets) == 1:
                bullets = split_sentences(bullets[0]) or bullets
            facts.append(make_fact(ids, kind, clean or title, bullets, dates=dates))  # type: ignore[arg-type]
    skills = []
    for line in sections.get("skills", []):
        name, _, items = line.partition(":")
        if items:
            skills.append(SkillGroup(name=name.strip(), items=[i.strip() for i in items.split(",") if i.strip()]))
    return Profile(identity=_identity(lines), summary=" ".join(sections.get("summary", [])), facts=facts, skills=skills)


def parse_cv_pdf(path: Path) -> Profile:
    """Parse a PDF CV file into a draft Profile."""
    return parse_cv_lines(pdf_lines(path))
