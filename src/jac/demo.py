"""`jac demo`: the whole pipeline on bundled fixture mails and the fictional example profile.

No account, no network: postings come from bundled pages placed in the cache, and the fetcher
is switched to offline mode so it cannot reach out even by mistake.
"""
from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from jac.config import Config
from jac.paths import Locations
from jac.pipeline import RunSummary, run
from jac.dashboard import write_dashboard
from jac.postings import PostingFetcher
from jac.profile import load_profile
from jac.queue import Queue
from jac.sources import LINKEDIN


def _copy_tree(package_dir: str, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in resources.files("jac").joinpath(package_dir).iterdir():
        if item.is_file():
            (target / item.name).write_bytes(item.read_bytes())


def run_demo(loc: Locations) -> tuple[RunSummary, Path, Queue]:
    """Run the demo in <data dir>/demo (rebuilt on every call) and return summary, dashboard, queue."""
    root = loc.data_dir / "demo"
    if root.exists():
        shutil.rmtree(root)
    _copy_tree("demo_data/eml", root / "mail")
    _copy_tree("demo_data/postings", root / "cache" / "postings")
    profile_path = root / "profile.yaml"
    profile_path.write_bytes(resources.files("jac").joinpath("demo_data/profile.example.yaml").read_bytes())

    cfg = Config()
    cfg.mail.provider = "gmail"
    cfg.fetch.offline = True
    profile = load_profile(profile_path)
    queue = Queue(root / cfg.paths.db)
    fetcher = PostingFetcher(cfg.fetch, root / "cache")
    summary = run(cfg, profile, _mails(root / "mail"), LINKEDIN, queue, fetcher, root / "out")
    return summary, write_dashboard(queue, root / "out"), queue


def _mails(folder: Path):
    from jac.ingest import read_eml_folder

    return read_eml_folder(folder)
