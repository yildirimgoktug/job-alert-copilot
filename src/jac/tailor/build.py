"""Build the full application package for one posting: CV (HTML and PDF) and cover note."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from jac.llm import LLMClient
from jac.postings import Posting, html_to_text
from jac.profile import Profile, find_never_claim
from jac.tailor.cover import write_cover_note
from jac.tailor.pdf import BrowserPageCounter, html_to_pdf
from jac.tailor.rank import keyword_rank, llm_rerank
from jac.tailor.render import ChainedPageCounter, HeuristicPageCounter, render_cv


@dataclass(frozen=True)
class Artifacts:
    folder: Path
    cv_html: Path
    cv_pdf: Path | None
    cover_note: Path
    pages: int | None
    fitted: bool
    cover_source: str
    notes: list[str] = field(default_factory=list)


def build_application(
    profile: Profile,
    posting: Posting,
    folder: Path,
    llm: LLMClient | None = None,
    browser: Path | None = None,
) -> Artifacts:
    """Write cv.html, cv.pdf (when a browser exists), cover_note.md and meta.json into folder."""
    folder.mkdir(parents=True, exist_ok=True)
    selection = keyword_rank(profile, posting)
    if llm is not None:
        selection = llm_rerank(llm, profile, posting, selection)
    estimate = HeuristicPageCounter()
    counter = ChainedPageCounter(BrowserPageCounter(browser), estimate) if browser else estimate
    cv = render_cv(profile, selection, counter)
    leaked = find_never_claim(html_to_text(cv.html), profile.never_claim)
    if leaked:
        raise ValueError(f"rendered CV contains never_claim strings: {sorted(leaked)}")
    cover = write_cover_note(profile, posting, selection, llm)

    cv_html = folder / "cv.html"
    cv_html.write_text(cv.html, encoding="utf-8")
    cv_pdf = folder / "cv.pdf" if browser and html_to_pdf(browser, cv_html, folder / "cv.pdf") else None
    cover_path = folder / "cover_note.md"
    cover_path.write_text(cover.text + "\n", encoding="utf-8")

    notes = []
    if browser is None:
        notes.append("No Chrome/Edge found: HTML only, page count is an estimate.")
    elif cv_pdf is None:
        notes.append("PDF conversion failed: HTML only.")
    if not cv.fitted:
        notes.append("Could not confirm a one-page CV; check it before sending.")
    if selection.dropped_ids:
        notes.append(f"LLM re-rank returned unknown ids that were dropped: {', '.join(selection.dropped_ids)}")
    if cover.source == "template":
        notes.append("Cover note comes from the fixed template" + (" after the LLM text was rejected." if cover.attempts else "."))
    meta = {
        "job_id": posting.job_id,
        "pages": cv.pages,
        "fitted": cv.fitted,
        "fact_ids": cv.fact_ids,
        "bullet_ids": cv.bullet_ids,
        "cover_source": cover.source,
        "cover_rejections": cover.rejections,
        "dropped_llm_ids": selection.dropped_ids,
    }
    (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return Artifacts(folder, cv_html, cv_pdf, cover_path, cv.pages, cv.fitted, cover.source, notes)
