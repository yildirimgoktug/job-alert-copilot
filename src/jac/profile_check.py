"""`jac profile check`: say in plain words what was understood and what is still missing."""
from __future__ import annotations

from jac.profile import Profile


def check_profile(profile: Profile) -> tuple[list[str], list[str]]:
    """Return (understood, missing). Missing items are things the tool needs or works better with."""
    c, ident = profile.constraints, profile.identity
    kinds = {k: sum(f.kind == k for f in profile.facts) for k in ("education", "experience", "project", "publication")}
    bullets = sum(len(f.bullets) for f in profile.facts)
    understood = [
        f"Name: {ident.name or '-'}; e-mail: {ident.email or '-'}",
        f"Facts: {kinds['education']} education, {kinds['experience']} experience, {kinds['project']} projects, "
        f"{kinds['publication']} publications ({bullets} bullets in total)",
        f"Skills groups: {len(profile.skills)}",
        "Languages spoken: " + (", ".join(f"{lang.name} ({lang.level})" for lang in c.languages) or "-"),
        "Languages you do NOT speak: " + (", ".join(c.languages_not_spoken) or "-"),
        f"Degree: {c.degree_level} {c.degree_field}".strip() + (f", graduation {c.graduation}" if c.graduation else ""),
        f"Max hours per week in term: {c.max_hours_per_week_in_term if c.max_hours_per_week_in_term else 'no limit'}",
        "Locations: " + (", ".join(c.locations) or "any") + ("; remote ok" if c.remote_ok else "; no remote"),
        f"Never claim: {len(profile.never_claim)} terms",
    ]
    checks = [
        (ident.name, "Your name is missing (identity.name)."),
        (ident.email, "Your e-mail address is missing (identity.email)."),
        (ident.headline, "No one-line headline (identity.headline); the CV header looks empty without it."),
        (profile.facts, "The fact bank is empty: import a CV (`jac import-cv`) or add facts by hand."),
        (bullets, "No bullets under any fact; the tailored CV would have nothing to show."),
        (profile.skills, "No skills listed."),
        (c.languages, "No languages spoken listed; language rules cannot work without them."),
        (c.graduation or c.degree_level == "none", "Graduation date is missing (constraints.graduation)."),
        (c.locations, "No locations; every location will pass."),
        (c.roles_of_interest, "No roles of interest; title matching is skipped."),
        (profile.never_claim, "No never_claim list; add technologies or employers you must never be credited with."),
    ]
    missing = [message for value, message in checks if not value]
    if kinds["experience"] + kinds["project"] == 0 and profile.facts:
        missing.append("There are no experience or project facts, only education.")
    return understood, missing
