"""Static dashboard: one HTML file listing what is prepared, what needs manual opening, what was skipped."""
from __future__ import annotations

import os
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from jac.queue import Job, Queue


def _rel(path: str, base: Path) -> str:
    return Path(os.path.relpath(path, base)).as_posix() if path else ""


def write_dashboard(queue: Queue, out_dir: Path) -> Path:
    """Render out/index.html. Links to artifacts are relative so the folder can be moved."""
    out_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=PackageLoader("jac", "templates"), autoescape=select_autoescape(["html", "j2"]))
    jobs = queue.list()

    def view(job: Job) -> dict:
        return {
            "job": job,
            "cv": _rel(job.cv_path or job.cv_html_path, out_dir),
            "cv_html": _rel(job.cv_html_path, out_dir),
            "cover": _rel(job.cover_path, out_dir),
        }

    html = env.get_template("dashboard.html.j2").render(
        prepared=[view(j) for j in jobs if j.verdict in ("fit", "borderline")],
        manual=[view(j) for j in jobs if j.verdict == "manual"],
        skipped=[view(j) for j in jobs if j.verdict == "reject"],
        quarantined=queue.quarantined(),
    )
    target = out_dir / "index.html"
    target.write_text(html, encoding="utf-8")
    return target
