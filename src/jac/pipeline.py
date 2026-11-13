"""The whole run: verify mail, fetch the public posting, judge fit, prepare artifacts, queue.

Nothing here applies, logs in or sends anything. The last step is files on disk and a row in
the queue; the human presses apply.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from jac.config import Config
from jac.fit.assess import assess
from jac.fit.rules import Verdict
from jac.ingest import RawMail
from jac.llm import LLMClient, OpenAICompatibleClient
from jac.postings import Posting, PostingFetcher, parse_posting
from jac.profile import Profile
from jac.queue import Job, Queue
from jac.sources import Source
from jac.tailor.cover import write_cover_note
from jac.tailor.pdf import BrowserPageCounter, find_browser, html_to_pdf
from jac.tailor.rank import keyword_rank, llm_rerank
from jac.tailor.render import ChainedPageCounter, HeuristicPageCounter, render_cv
from jac.verify import verify_message

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    mails: int = 0
    quarantined: list[tuple[str, list[str]]] = field(default_factory=list)
    prepared: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    already_queued: int = 0
    halted: str = ""


def llm_from_config(cfg: Config) -> LLMClient | None:
    """The optional local LLM client, only when the user switched it on."""
    return OpenAICompatibleClient(cfg.llm) if cfg.llm.enabled else None


def _prepare_artifacts(
    profile: Profile, posting: Posting, cfg: Config, out_dir: Path, llm: LLMClient | None
) -> tuple[str, str, str]:
    """Write cv.html, cv.pdf (if a browser exists) and cover_note.md; return their paths."""
    folder = out_dir / "jobs" / posting.job_id
    folder.mkdir(parents=True, exist_ok=True)
    selection = keyword_rank(profile, posting)
    if llm is not None:
        selection = llm_rerank(llm, profile, posting, selection)
    browser = find_browser(cfg.pdf.browser_path)
    counter = ChainedPageCounter(BrowserPageCounter(browser), HeuristicPageCounter()) if browser else HeuristicPageCounter()
    cv = render_cv(profile, selection, counter)
    html_path = folder / "cv.html"
    html_path.write_text(cv.html, encoding="utf-8")
    pdf_path = folder / "cv.pdf"
    has_pdf = bool(browser) and html_to_pdf(browser, html_path, pdf_path)
    cover = write_cover_note(profile, posting, selection, llm)
    cover_path = folder / "cover_note.md"
    cover_path.write_text(cover.text + "\n", encoding="utf-8")
    return str(pdf_path if has_pdf else ""), str(html_path), str(cover_path)


def run(
    cfg: Config,
    profile: Profile,
    mails: Iterable[RawMail],
    source: Source,
    queue: Queue,
    fetcher: PostingFetcher,
    out_dir: Path,
    llm: LLMClient | None = None,
) -> RunSummary:
    """Process every mail. Safe to repeat: queued jobs are skipped, quarantined mails stay out."""
    summary = RunSummary()
    trusted = cfg.mail.authserv_ids()
    for mail in mails:
        summary.mails += 1
        verdict = verify_message(mail.parse(), source, trusted)
        if not verdict.accepted:
            queue.quarantine(mail.mail_id, verdict.sender, verdict.reasons)
            summary.quarantined.append((mail.mail_id, verdict.reasons))
            continue
        for url in verdict.job_urls:
            job_id = source.job_id(url)
            existing = queue.get(job_id, source.name)
            if existing and existing.verdict != "manual":
                summary.already_queued += 1
                continue
            job = _process(cfg, profile, source, url, job_id, mail.mail_id, fetcher, out_dir, llm)
            if existing:
                queue.discard(job_id, source.name)
            queue.add(job)
            bucket = {"manual": summary.manual, "reject": summary.skipped}.get(job.verdict, summary.prepared)
            bucket.append(job_id)
    summary.halted = fetcher.halted_reason
    return summary


def _process(cfg: Config, profile: Profile, source: Source, url: str, job_id: str, mail_id: str,
             fetcher: PostingFetcher, out_dir: Path, llm: LLMClient | None) -> Job:
    base = dict(job_id=job_id, source=source.name, url=url, mail_id=mail_id)
    fetched = fetcher.fetch(job_id)
    posting = parse_posting(fetched.html, job_id) if fetched.usable and fetched.html else None
    if posting is None:
        reason = fetched.reason or "posting could not be read"
        return Job(**base, verdict=Verdict.MANUAL.value, status="new", reasons=[f"Open it manually: {reason}"])
    result = assess(profile, posting, llm)
    info = dict(title=posting.title, company=posting.company, location=posting.location, reasons=result.reasons())
    if result.verdict is Verdict.REJECT:
        return Job(**base, **info, verdict=result.verdict.value, status="skipped")
    pdf, html, cover = _prepare_artifacts(profile, posting, cfg, out_dir, llm)
    return Job(**base, **info, verdict=result.verdict.value, status="prepared",
               cv_path=pdf, cv_html_path=html, cover_path=cover)
