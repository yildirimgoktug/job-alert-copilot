"""Deterministic CV rendering from the fact bank, fitted to one page.

Nothing is generated here: the template only prints facts the user wrote. Fitting drops the
lowest-priority bullets first (never the last bullet of an entry), then whole projects and
publications, until the page counter says the CV fits on one page.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup, escape

from jac.postings import html_to_text
from jac.profile import Fact, Profile
from jac.tailor.rank import Selection


class PageCounter(Protocol):
    def count(self, html: str) -> int | None:
        """Number of printed pages, or None if it cannot be determined."""


class HeuristicPageCounter:
    """Rough page estimate from the amount of text, used when no browser is available."""

    chars_per_line = 118
    lines_per_page = 72  # calibrated against headless Chrome on a dense one-page CV

    def count(self, html: str) -> int:
        lines = sum(max(1, math.ceil(len(line) / self.chars_per_line)) for line in html_to_text(html).splitlines())
        return max(1, math.ceil(lines / self.lines_per_page))


class ChainedPageCounter:
    """Use the primary counter (a real browser) and fall back to the estimate when it fails."""

    def __init__(self, primary: PageCounter, fallback: PageCounter) -> None:
        self._primary, self._fallback = primary, fallback

    def count(self, html: str) -> int | None:
        result = self._primary.count(html)
        return result if result is not None else self._fallback.count(html)


@dataclass(frozen=True)
class RenderedCV:
    html: str
    fact_ids: list[str]
    bullet_ids: list[str]
    pages: int | None
    fitted: bool


_BOLD = re.compile(r"\*\*(.+?)\*\*")
_SCHEME = re.compile(r"^https?://(www\.)?")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")


def rich(text: str) -> Markup:
    """Escape text, then turn **bold** and *italic* into tags. Only these two are supported."""
    escaped = str(escape(text))
    return Markup(_ITALIC.sub(r"<i>\1</i>", _BOLD.sub(r"<b>\1</b>", escaped)))


_ENV = Environment(loader=PackageLoader("jac", "templates"), autoescape=select_autoescape(["html", "j2"]))
_ENV.filters["rich"] = rich


def _contact_html(profile: Profile) -> Markup:
    ident = profile.identity
    first = [str(escape(p)) for p in (ident.location, ident.phone) if p]
    if ident.email:
        first.append(f'<a href="mailto:{escape(ident.email)}">{escape(ident.email)}</a>')
    links = [f'<a href="{escape(url)}">{escape(_SCHEME.sub("", url))}</a>' for url in ident.links.values()]
    sep = " &nbsp;·&nbsp; "
    return Markup(sep.join(first) + ("<br>" + sep.join(links) if links else ""))


def _trim(fact: Fact, keep: set[str], order: list[str]) -> Fact:
    """A copy of the fact with only the kept bullets, in ranked order."""
    by_id = {b.id: b for b in fact.bullets}
    return fact.model_copy(update={"bullets": [by_id[i] for i in order if i in keep and i in by_id]})


def _ranked_ids(profile: Profile, selection: Selection, kind: str) -> list[str]:
    return [fid for fid in selection.fact_order if (fact := profile.fact_by_id(fid)) and fact.kind == kind]


def _html(profile: Profile, facts: list[Fact]) -> str:
    def of(kind: str) -> list[Fact]:
        return [f for f in facts if f.kind == kind]

    return _ENV.get_template("cv.html.j2").render(
        identity=profile.identity,
        contact_html=_contact_html(profile),
        summary=profile.summary,
        education=of("education"),
        experience=of("experience"),
        projects=of("project"),
        publications=of("publication"),
        skills=profile.skills,
    )


def render_cv(
    profile: Profile,
    selection: Selection,
    counter: PageCounter,
    max_projects: int = 4,
    max_publications: int = 2,
) -> RenderedCV:
    """Render the CV for a selection and shrink it until it is one page (or nothing is left to drop)."""
    included = {
        "project": _ranked_ids(profile, selection, "project")[:max_projects],
        "publication": _ranked_ids(profile, selection, "publication")[:max_publications],
    }
    keep: set[str] = {b.id for f in profile.facts for b in f.bullets}
    # The best bullet of every entry is never dropped on its own.
    protected = {(selection.bullet_order.get(f.id) or [b.id for b in f.bullets])[0] for f in profile.facts if f.bullets}

    def build() -> tuple[str, list[Fact]]:
        facts = [f for f in profile.facts if f.kind not in included]
        facts += [f for kind in included for fid in included[kind] if (f := profile.fact_by_id(fid))]
        trimmed = [_trim(f, keep, selection.bullet_order.get(f.id) or [b.id for b in f.bullets]) for f in facts]
        return _html(profile, trimmed), trimmed

    while True:
        html, facts = build()
        pages = counter.count(html)
        if pages is None or pages <= 1:
            return RenderedCV(html, [f.id for f in facts], sorted(keep), pages, pages is not None)
        droppable = [b for b in reversed(selection.priority) if b in keep and b not in protected]
        if droppable:
            keep.discard(droppable[0])
            continue
        for kind in ("project", "publication"):
            if len(included[kind]) > 1:
                dropped = profile.fact_by_id(included[kind].pop())
                keep -= {b.id for b in dropped.bullets} if dropped else set()
                break
        else:
            return RenderedCV(html, [f.id for f in facts], sorted(keep), pages, False)
