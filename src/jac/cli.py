"""Command line entry point `jac`.

The furthest this tool goes is opening a posting URL in your default browser when you ask it
to. It never applies, never logs in to a job site and never sends mail.
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from pydantic import ValidationError

from jac import doctor as doctor_mod
from jac import paths, wizard
from jac.config import ConfigError
from jac.dashboard import write_dashboard
from jac.demo import run_demo
from jac.importers.cv_html import parse_cv_html
from jac.importers.cv_pdf import parse_cv_pdf
from jac.importers.draft import merge_into_profile
from jac.importers.linkedin_export import parse_linkedin_export
from jac.pipeline import RunSummary, llm_from_config, run
from jac.postings import PostingFetcher
from jac.profile import load_profile
from jac.profile_check import check_profile
from jac.queue import Job, Queue
from jac.readers import build_reader
from jac.sources import SOURCES


def _summary_lines(summary: RunSummary) -> list[str]:
    lines = [
        f"Mails read: {summary.mails}; quarantined: {len(summary.quarantined)}",
        f"Prepared: {len(summary.prepared)}; skipped by rules: {len(summary.skipped)}; "
        f"open manually: {len(summary.manual)}; already queued: {summary.already_queued}",
    ]
    lines += [f"  quarantined {mail_id}: {'; '.join(reasons)}" for mail_id, reasons in summary.quarantined]
    if summary.halted:
        lines.append(f"Fetching stopped: {summary.halted}")
    return lines


def _job_line(job: Job) -> str:
    return f"{job.job_id}  {job.verdict:10} {job.status:9} {job.title or '(not read)'} - {job.company}"


def cmd_init(args: argparse.Namespace) -> int:
    loc = paths.locations(args.home)
    config_file, profile_file = wizard.run_wizard(loc, Path(args.answers) if args.answers else None, force=args.force)
    print(f"Wrote {config_file}\nWrote {profile_file}")
    print("Next: `jac import-cv your_cv.pdf` (or .html), then `jac profile check`, then `jac doctor`.")
    return 0


def _import(args: argparse.Namespace, draft_builder) -> int:
    loc = paths.locations(args.home)
    loc.config_dir.mkdir(parents=True, exist_ok=True)
    merged = merge_into_profile(draft_builder(Path(args.path)), loc.profile_file)
    print(f"Draft written to {loc.profile_file}: {len(merged.facts)} facts. Review it, then run `jac profile check`.")
    return 0


def cmd_import_cv(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.suffix.lower() == ".pdf":
        return _import(args, parse_cv_pdf)
    return _import(args, lambda p: parse_cv_html(p.read_text(encoding="utf-8")))


def cmd_import_export(args: argparse.Namespace) -> int:
    return _import(args, parse_linkedin_export)


def cmd_profile_check(args: argparse.Namespace) -> int:
    loc = paths.locations(args.home)
    understood, missing = check_profile(load_profile(loc.profile_file))
    print("What was understood:")
    print("\n".join(f"  - {line}" for line in understood))
    print("\nWhat is missing:" if missing else "\nNothing is missing.")
    print("\n".join(f"  - {line}" for line in missing))
    return 1 if missing else 0


def cmd_run(args: argparse.Namespace) -> int:
    loc, cfg = paths.load(args.home)
    profile = load_profile(loc.profile_file)
    source = SOURCES[args.site]
    mails = build_reader(cfg, source, loc.config_dir, args.source, Path(args.eml_dir) if args.eml_dir else None)
    queue = Queue(loc.db(cfg))
    fetcher = PostingFetcher(cfg.fetch, loc.cache(cfg))
    summary = run(cfg, profile, mails, source, queue, fetcher, loc.out(cfg), llm_from_config(cfg))
    print("\n".join(_summary_lines(summary)))
    print(f"Dashboard: {write_dashboard(queue, loc.out(cfg))}")
    return 0


def _queue(args: argparse.Namespace) -> tuple[paths.Locations, Queue]:
    loc, cfg = paths.load(args.home)
    return loc, Queue(loc.db(cfg))


def cmd_list(args: argparse.Namespace) -> int:
    _, queue = _queue(args)
    jobs = queue.list(args.status)
    print("\n".join(_job_line(j) for j in jobs) if jobs else "No jobs yet. Run `jac run` or `jac demo`.")
    return 0


def _find(queue: Queue, job_id: str) -> Job:
    job = queue.get(job_id)
    if job is None:
        raise ConfigError(f"no job {job_id} in the queue; see `jac list`")
    return job


def cmd_show(args: argparse.Namespace) -> int:
    _, queue = _queue(args)
    job = _find(queue, args.job_id)
    print(_job_line(job), job.url, sep="\n")
    print("\n".join(f"  {r}" for r in job.reasons))
    for label, value in (("CV", job.cv_path or job.cv_html_path), ("Cover note", job.cover_path)):
        if value:
            print(f"{label}: {value}")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    """Open the canonical posting URL in the default browser. This is as far as the tool goes."""
    _, queue = _queue(args)
    job = _find(queue, args.job_id)
    source = SOURCES.get(job.source)
    if source is None or source.canonicalize(job.url) != job.url:
        raise ConfigError(f"refusing to open {job.url!r}: it is not a canonical posting URL")
    webbrowser.open(job.url)
    if job.status in ("new", "prepared"):
        queue.set_status(job.job_id, "opened", job.source)
    print(f"Opened {job.url}. Press apply yourself, then `jac mark {job.job_id} applied`.")
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    _, queue = _queue(args)
    job = _find(queue, args.job_id)
    queue.set_status(job.job_id, args.status, job.source)
    print(f"{job.job_id} marked {args.status}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    loc, cfg = paths.load(args.home)
    target = write_dashboard(Queue(loc.db(cfg)), loc.out(cfg))
    print(target)
    if args.open:
        webbrowser.open(target.as_uri())
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = doctor_mod.run_doctor(paths.locations(args.home))
    print(doctor_mod.format_report(checks))
    return 1 if any(c.status == doctor_mod.FAIL for c in checks) else 0


def cmd_demo(args: argparse.Namespace) -> int:
    summary, dashboard, queue = run_demo(paths.locations(args.home))
    print("\n".join(_summary_lines(summary)))
    print("\n".join(_job_line(j) for j in queue.list()))
    print(f"Dashboard: {dashboard}")
    if not args.no_open:
        webbrowser.open(dashboard.as_uri())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jac", description="Prepare job applications from your own verified alert mails. You press apply.")
    parser.add_argument("--home", help="keep config, data and output in this folder (also JAC_HOME)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)
        return p

    p = add("init", cmd_init, "ask a few questions and create config and profile")
    p.add_argument("--answers", help="YAML file with the answers instead of asking")
    p.add_argument("--force", action="store_true", help="overwrite an existing profile")
    p = add("import-cv", cmd_import_cv, "draft the fact bank from a CV (.html or .pdf)")
    p.add_argument("path")
    p = add("import-linkedin-export", cmd_import_export, "draft the fact bank from the LinkedIn data export (folder or zip)")
    p.add_argument("path")
    prof = add("profile", cmd_profile_check, "profile tools")
    prof.add_argument("action", choices=["check"])
    p = add("run", cmd_run, "read mails, judge fit, prepare CVs and notes")
    p.add_argument("--source", choices=["eml", "imap", "gmail"], help="mail method (default: from config)")
    p.add_argument("--eml-dir", help="folder with .eml files")
    p.add_argument("--site", choices=sorted(SOURCES), default="linkedin")
    p = add("list", cmd_list, "list queued jobs")
    p.add_argument("--status", choices=["new", "prepared", "opened", "applied", "skipped"])
    add("show", cmd_show, "show one job").add_argument("job_id")
    add("open", cmd_open, "open the posting in your default browser").add_argument("job_id")
    p = add("mark", cmd_mark, "record what you did")
    p.add_argument("job_id")
    p.add_argument("status", choices=["applied", "skipped"])
    add("dashboard", cmd_dashboard, "write out/index.html").add_argument("--open", action="store_true")
    add("doctor", cmd_doctor, "check the setup and say what to fix")
    add("demo", cmd_demo, "run everything on bundled fake data").add_argument("--no-open", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, ValidationError, FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
