import re
import subprocess
from datetime import date
from pathlib import Path

import pytest
from conftest import ROOT

from jac.config import Config
from jac.dashboard import write_dashboard
from jac.ingest import GMAIL_SCOPE, build_gmail_query, read_eml_folder
from jac.profile import load_profile
from jac.queue import Job, Queue
from jac.sources import LINKEDIN
from jac.tailor.pdf import BrowserPageCounter, count_pdf_pages, find_browser
from jac.tailor.rank import keyword_rank
from jac.tailor.render import render_cv
from jac.verify import verify_message

TRUSTED = ["mx.google.com"]


def test_example_alert_mails_are_classified_as_documented():
    results = {m.mail_id: verify_message(m.parse(), LINKEDIN, TRUSTED) for m in read_eml_folder(ROOT / "examples" / "eml")}
    accepted = {k for k, v in results.items() if v.accepted}
    assert accepted == {"eml:01_authentic_alert.eml", "eml:02_authentic_alert.eml"}
    first = results["eml:01_authentic_alert.eml"]
    assert first.job_urls == [f"https://www.linkedin.com/jobs/view/390000000{i}" for i in (1, 2, 3, 4)]
    assert any("lnkd.in" in url for url in first.rejected_links)
    for mail_id in set(results) - accepted:
        assert results[mail_id].job_urls == []  # quarantined mails are never parsed for links


def test_eml_reader_yields_raw_bytes_with_real_headers(tmp_path):
    (tmp_path / "b.eml").write_bytes(b"From: a@example.com\r\n\r\nbody")
    (tmp_path / "a.eml").write_bytes(b"From: z@example.com\r\n\r\nbody")
    (tmp_path / "notes.txt").write_text("ignored")
    mails = list(read_eml_folder(tmp_path))
    assert [m.mail_id for m in mails] == ["eml:a.eml", "eml:b.eml"]
    assert mails[0].raw.startswith(b"From: z@example.com") and mails[0].parse()["From"] == "z@example.com"


def test_gmail_query_is_limited_to_senders_and_a_date_window():
    query = build_gmail_query(list(LINKEDIN.allowed_senders), date(2026, 11, 13), 7)
    assert query == "from:(jobalerts-noreply@linkedin.com OR jobs-noreply@linkedin.com OR jobs-listings@linkedin.com) after:2026/11/06"
    assert GMAIL_SCOPE.endswith("gmail.readonly")


def test_pdf_page_counter_does_not_count_the_pages_tree():
    pdf = b"<< /Type /Catalog >> << /Type /Pages /Kids [3 0 R 4 0 R] >> << /Type /Page >> << /Type/Page /Parent 2 0 R >>"
    assert count_pdf_pages(pdf) == 2


def test_dashboard_escapes_untrusted_posting_text(tmp_path):
    queue = Queue(tmp_path / "q.sqlite")
    queue.add(Job(job_id="1", source="linkedin", url="https://www.linkedin.com/jobs/view/1", verdict="fit", status="prepared",
                  title="<script>alert(1)</script>", company="<b>Evil</b>", reasons=['[note] "<img src=x onerror=alert(2)>"']))
    page = write_dashboard(queue, tmp_path / "out").read_text(encoding="utf-8")
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page
    assert "<img src=x" not in page and "<b>Evil</b>" not in page
    assert "https://www.linkedin.com/jobs/view/1" in page


@pytest.mark.skipif(find_browser() is None, reason="needs a local Chrome or Edge")
def test_real_browser_counts_pages_of_the_rendered_cv(profile, load_posting):
    selection = keyword_rank(profile, load_posting("3900000001"))
    cv = render_cv(profile, selection, BrowserPageCounter(find_browser()))
    assert cv.pages == 1 and cv.fitted


def test_default_config_has_polite_fetch_limits():
    fetch = Config().fetch
    assert fetch.min_interval_seconds >= 10 and fetch.max_requests_per_run == 20
    assert "@" not in fetch.user_agent and "job-alert-copilot" in fetch.user_agent


def _tracked_files() -> list[str]:
    try:
        return subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")


def test_private_files_are_not_tracked():
    private = {"profile.yaml", "config.yaml", "token.json", "client_secret.json", "jac.sqlite"}
    assert not [n for n in _tracked_files() if Path(n).name in private or n.startswith(("out/", "cache/"))]


def _owner_strings(profile_file: Path) -> list[str]:
    """Strings from the owner's own (git-ignored) profile that must never be committed.

    They are read at test time, so the test itself holds no personal data. The name is not
    listed because LICENSE and pyproject.toml carry it on purpose.
    """
    profile = load_profile(profile_file)
    ident = profile.identity
    strings = [ident.email, ident.email.split("@")[0], ident.phone, ident.phone.replace(" ", "")]
    strings += [url.rstrip("/").rsplit("/", 1)[-1] for key, url in ident.links.items() if key != "github"]
    strings += [re.split(r"[,(]", f.org)[0].strip() for f in profile.facts if f.org]
    return [s.lower() for s in strings if len(s) >= 5]


def test_tracked_files_hold_none_of_the_owners_details():
    profile_file = ROOT / "profile.yaml"
    if not profile_file.exists():
        pytest.skip("no local profile.yaml to compare against")
    forbidden = _owner_strings(profile_file)
    for name in _tracked_files():
        path = ROOT / name
        if not path.is_file() or path.suffix in {".pdf", ".png"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        assert not [f for f in forbidden if f in text], name
