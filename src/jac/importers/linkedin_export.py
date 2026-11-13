"""Read the official LinkedIn "Download your data" archive into a draft profile.

Uses only the CSV files LinkedIn gives you (Profile, Positions, Education, Skills, Projects),
from a folder or the .zip itself. No scraping and no network.
"""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from jac.importers.draft import IdFactory, make_fact, split_description
from jac.profile import Fact, Identity, Profile, SkillGroup


def _read_rows(source: Path, name: str) -> list[dict[str, str]]:
    if source.is_dir():
        path = source / name
        raw = path.read_bytes() if path.exists() else b""
    else:
        with zipfile.ZipFile(source) as archive:
            match = next((n for n in archive.namelist() if n.rsplit("/", 1)[-1] == name), None)
            raw = archive.read(match) if match else b""
    text = raw.decode("utf-8-sig", errors="replace")
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(text))]


def _dates(row: dict[str, str], start: str, end: str) -> str:
    first, last = row.get(start, ""), row.get(end, "")
    return f"{first} – {last or 'Present'}" if first else ""


def parse_linkedin_export(source: Path) -> Profile:
    """Build a draft Profile from the export folder or zip."""
    ids = IdFactory()
    profile_rows = _read_rows(source, "Profile.csv")
    row = profile_rows[0] if profile_rows else {}
    identity = Identity(
        name=f"{row.get('First Name', '')} {row.get('Last Name', '')}".strip(),
        headline=row.get("Headline", ""),
        location=row.get("Geo Location", ""),
    )
    facts: list[Fact] = []
    for pos in _read_rows(source, "Positions.csv"):
        title = pos.get("Title", "") or pos.get("Company Name", "")
        facts.append(make_fact(ids, "experience", title, split_description(pos.get("Description", "")),
                               org=pos.get("Company Name", ""), dates=_dates(pos, "Started On", "Finished On")))
    for edu in _read_rows(source, "Education.csv"):
        title = edu.get("Degree Name", "") or edu.get("School Name", "")
        facts.append(make_fact(ids, "education", title, split_description(edu.get("Notes", "")),
                               org=edu.get("School Name", ""), dates=_dates(edu, "Start Date", "End Date")))
    for proj in _read_rows(source, "Projects.csv"):
        facts.append(make_fact(ids, "project", proj.get("Title", "Project"), split_description(proj.get("Description", "")),
                               link=proj.get("Url", ""), dates=_dates(proj, "Started On", "Finished On")))
    names = [r["Name"] for r in _read_rows(source, "Skills.csv") if r.get("Name")]
    skills = [SkillGroup(name="Skills", items=names)] if names else []
    return Profile(identity=identity, summary=row.get("Summary", ""), facts=facts, skills=skills)
