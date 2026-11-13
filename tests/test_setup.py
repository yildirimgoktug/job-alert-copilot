"""Wizard, IMAP (fake connection), keyring (mocked), doctor, demo, queue and pipeline behaviour."""
from datetime import date
from pathlib import Path

import pytest
import requests
import yaml

from jac import cli, doctor, keyring_store, wizard
from jac.config import Config, ConfigError, ImapConfig, load_config
from jac.demo import run_demo
from jac.imap import ImapReader
from jac.paths import Locations
from jac.pipeline import run
from jac.postings import PostingFetcher
from jac.profile import load_profile
from jac.queue import Job, Queue
from jac.sources import LINKEDIN
from mailfactory import build_mail, job_link

ANSWERS = {
    "name": "Sam Rivers", "email": "sam@example.com", "headline": "BSc student",
    "languages_spoken": "English:native, German:B1", "languages_not_spoken": "Dutch, French",
    "degree_level": "master", "degree_field": "Data Science", "graduation": "2027-06-30",
    "max_hours": "20", "locations": "Berlin, Remote", "roles": "data intern", "never_claim": "Rust, Kubernetes",
}


def loc_in(tmp_path: Path) -> Locations:
    return Locations(tmp_path, tmp_path)


# ---- wizard -------------------------------------------------------------------------------

def test_wizard_from_answers_file(tmp_path):
    answers = tmp_path / "answers.yaml"
    answers.write_text(yaml.safe_dump(ANSWERS), encoding="utf-8")
    config_file, profile_file = wizard.run_wizard(loc_in(tmp_path / "cfg"), answers)
    profile = load_profile(profile_file)
    c = profile.constraints
    assert [(x.name, x.level) for x in c.languages] == [("English", "native"), ("German", "B1")]
    assert c.languages_not_spoken == ["Dutch", "French"] and c.degree_level == "master"
    assert c.graduation == date(2027, 6, 30) and c.max_hours_per_week_in_term == 20
    assert c.locations == ["Berlin", "Remote"] and profile.never_claim == ["Rust", "Kubernetes"]
    cfg = load_config(config_file)
    assert cfg.mail.method == "eml" and cfg.mail.authserv_ids() == ["mx.google.com"]
    assert (tmp_path / "cfg" / "mail").is_dir()


def test_wizard_refuses_overwrite_unknown_keys_and_bad_choices(tmp_path):
    loc = loc_in(tmp_path)
    answers = tmp_path / "a.yaml"
    answers.write_text(yaml.safe_dump(ANSWERS), encoding="utf-8")
    wizard.run_wizard(loc, answers)
    with pytest.raises(ConfigError, match="already exists"):
        wizard.run_wizard(loc, answers)
    answers.write_text(yaml.safe_dump({"nme": "typo"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown answers"):
        wizard.run_wizard(loc, answers, force=True)
    with pytest.raises(ConfigError, match="degree_level"):
        wizard.build({"degree_level": "wizard"})


def test_wizard_interactive_uses_defaults_and_reasks_bad_choice(tmp_path):
    replies = iter(["Sam", "sam@example.com", "", "", "", "", "", "doctorate", "phd"])
    wizard.run_wizard(loc_in(tmp_path), ask=lambda prompt: next(replies, ""))
    profile = load_profile(tmp_path / "profile.yaml")
    assert profile.constraints.degree_level == "phd"
    assert [lang.name for lang in profile.constraints.languages] == ["English"]


def test_wizard_imap_needs_a_user():
    with pytest.raises(ConfigError):
        wizard.build({"mail_method": "imap", "mail_provider": "yahoo"})
    cfg, _ = wizard.build({"mail_method": "imap", "mail_provider": "yahoo", "mail_user": "sam@yahoo.com"})
    assert cfg.mail.imap.host == "imap.mail.yahoo.com" and cfg.mail.imap.user == "sam@yahoo.com"


# ---- IMAP and keyring ---------------------------------------------------------------------

class FakeImap:
    """Records every call. Any method that could change a mailbox raises instead."""

    def __init__(self, raw: list[bytes]) -> None:
        self.raw, self.log = raw, []

    def login(self, user, password):
        self.log.append(("login", user))
        return "OK", []

    def select(self, folder="INBOX", readonly=False):
        self.log.append(("select", folder, readonly))
        return "OK", [str(len(self.raw)).encode()]

    def uid(self, command, *args):
        self.log.append(("uid", command.upper(), args))
        if command.upper() == "SEARCH":
            return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.raw)))]
        index = int(args[0]) - 1
        return "OK", [(b"1 (BODY[] {10}", self.raw[index]), b")"]

    def logout(self):
        self.log.append(("logout",))
        return "BYE", []

    def __getattr__(self, name):
        raise AssertionError(f"IMAP method {name} must never be used")


def test_imap_reader_is_strictly_read_only():
    fake = FakeImap([build_mail(), build_mail([job_link("3900000002")])])
    reader = ImapReader(ImapConfig(host="h", user="u", window_days=3), "linkedin.com", lambda: "pw",
                        connect=lambda host, port: fake, today=date(2026, 11, 13))
    mails = list(reader.read())
    assert [m.mail_id for m in mails] == ["imap:INBOX:1", "imap:INBOX:2"]
    assert mails[0].raw == fake.raw[0]
    assert ("select", "INBOX", True) in fake.log
    commands = {entry[1] for entry in fake.log if entry[0] == "uid"}
    assert commands == {"SEARCH", "FETCH"}
    fetches = [entry for entry in fake.log if entry[:2] == ("uid", "FETCH")]
    assert all(entry[2][1] == "(BODY.PEEK[])" for entry in fetches)
    search = next(entry for entry in fake.log if entry[:2] == ("uid", "SEARCH"))
    assert "10-Nov-2026" in search[2] and "linkedin.com" in search[2]
    assert {entry[0] for entry in fake.log} <= {"login", "select", "uid", "logout"}
    assert fake.log[-1] == ("logout",)


def test_imap_check_counts_messages_read_only():
    fake = FakeImap([build_mail()] * 3)
    reader = ImapReader(ImapConfig(host="h", user="u"), "linkedin.com", lambda: "pw", connect=lambda h, p: fake)
    assert reader.check() == 3
    assert all(entry[2] is True for entry in fake.log if entry[0] == "select")


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, password):
        self.store[(service, user)] = password


def test_password_is_asked_once_and_kept_only_in_the_keyring(monkeypatch, tmp_path, capsys):
    fake = FakeKeyring()
    monkeypatch.setattr(keyring_store, "keyring", fake)
    asked = []
    assert keyring_store.password_for("sam@x.com", ask=lambda p: asked.append(p) or "s3cret-app-pw") == "s3cret-app-pw"
    assert keyring_store.password_for("sam@x.com", ask=lambda p: pytest.fail("asked twice")) == "s3cret-app-pw"
    assert len(asked) == 1 and fake.store == {("job-alert-copilot", "sam@x.com"): "s3cret-app-pw"}
    assert "s3cret" not in "".join(asked) and "s3cret" not in capsys.readouterr().out
    with pytest.raises(RuntimeError):
        keyring_store.password_for("new@x.com", ask=lambda p: "")


def test_config_has_no_password_field():
    assert "password" not in " ".join(Config.model_fields) + " ".join(ImapConfig.model_fields)


# ---- doctor -------------------------------------------------------------------------------

def test_doctor_on_an_empty_machine_says_what_to_do(tmp_path):
    checks = doctor.run_doctor(loc_in(tmp_path))
    report = doctor.format_report(checks)
    by_name = {c.name: c for c in checks}
    assert by_name["profile"].status == doctor.FAIL and "jac init" in report
    assert by_name["trusted authserv-id"].status == doctor.FAIL
    assert by_name["local LLM"].detail.startswith("not enabled")
    assert "What to fix:" in report


def test_doctor_after_init_is_mostly_green_and_never_probes_a_disabled_llm(tmp_path):
    answers = tmp_path / "a.yaml"
    answers.write_text(yaml.safe_dump({**ANSWERS, "mail_provider": "gmail"}), encoding="utf-8")
    loc = loc_in(tmp_path / "c")
    wizard.run_wizard(loc, answers)
    (loc.config_dir / "mail" / "x.eml").write_bytes(build_mail())

    def no_probe(url):
        raise AssertionError("LLM must not be called when it is not configured")

    checks = {c.name: c for c in doctor.run_doctor(loc, probe_llm=no_probe)}
    assert checks["mail folder"].status == doctor.OK and "1 .eml" in checks["mail folder"].detail
    assert checks["trusted authserv-id"].status == doctor.OK
    assert checks["profile"].status == doctor.WARN


def test_doctor_checks_imap_and_llm_through_injected_probes(tmp_path):
    loc = loc_in(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "mail": {"method": "imap", "provider": "fastmail", "imap": {"user": "sam@fastmail.com"}},
        "llm": {"enabled": True, "base_url": "http://localhost:9/v1"},
    }), encoding="utf-8")
    checks = {c.name: c for c in doctor.run_doctor(loc, imap_count=lambda cfg: 7, probe_llm=lambda url: 200)}
    assert checks["IMAP (read-only)"].status == doctor.OK and "7 messages" in checks["IMAP (read-only)"].detail
    assert checks["local LLM"].status == doctor.OK

    def refuse(url):
        raise requests.ConnectionError()

    checks = {c.name: c for c in doctor.run_doctor(loc, imap_count=lambda cfg: 1 / 0, probe_llm=refuse)}
    assert checks["IMAP (read-only)"].status == doctor.FAIL
    assert checks["local LLM"].status == doctor.WARN and "keyword mode" in checks["local LLM"].fix


# ---- queue and pipeline -------------------------------------------------------------------

def test_queue_is_idempotent_and_tracks_status(tmp_path):
    queue = Queue(tmp_path / "q.sqlite")
    job = Job("1", "linkedin", "https://www.linkedin.com/jobs/view/1", "fit", "prepared", reasons=["r"])
    assert queue.add(job) and not queue.add(job)
    assert len(queue.list()) == 1 and queue.get("1").reasons == ["r"]
    assert queue.set_status("1", "opened") and queue.get("1").status == "opened"
    with pytest.raises(ValueError):
        queue.set_status("1", "sent")
    assert [j.job_id for j in queue.list("opened")] == ["1"] and queue.list("applied") == []


def _demo_parts(tmp_path):
    import shutil
    from importlib import resources

    root = tmp_path / "w"
    shutil.copytree(resources.files("jac").joinpath("demo_data/postings"), root / "cache" / "postings")
    profile = load_profile(Path(str(resources.files("jac").joinpath("demo_data/profile.example.yaml"))))
    cfg = Config()
    cfg.mail.provider = "gmail"
    cfg.fetch.offline = True
    return root, profile, cfg


def test_running_twice_does_not_duplicate_jobs(tmp_path):
    root, profile, cfg = _demo_parts(tmp_path)
    queue = Queue(root / "q.sqlite")
    mails = [type("M", (), {})]  # replaced below
    from jac.ingest import RawMail

    mails = [RawMail("m1", build_mail([job_link("3900000001")]))]
    fetcher = PostingFetcher(cfg.fetch, root / "cache")
    first = run(cfg, profile, mails, LINKEDIN, queue, fetcher, root / "out")
    second = run(cfg, profile, mails, LINKEDIN, queue, fetcher, root / "out")
    assert first.prepared == ["3900000001"] and second.prepared == [] and second.already_queued == 1
    assert len(queue.list()) == 1


def test_unreadable_posting_becomes_open_manually(tmp_path):
    root, profile, cfg = _demo_parts(tmp_path)
    from jac.ingest import RawMail

    queue = Queue(root / "q.sqlite")
    mails = [RawMail("m1", build_mail([job_link("3999999999")]))]
    summary = run(cfg, profile, mails, LINKEDIN, queue, PostingFetcher(cfg.fetch, root / "cache"), root / "out")
    job = queue.get("3999999999")
    assert summary.manual == ["3999999999"] and job.verdict == "manual" and job.status == "new"


def test_pipeline_refuses_without_trusted_authserv(tmp_path):
    root, profile, cfg = _demo_parts(tmp_path)
    cfg.mail.provider = ""
    from jac.ingest import RawMail

    with pytest.raises(ConfigError):
        run(cfg, profile, [RawMail("m", build_mail())], LINKEDIN, Queue(root / "q.sqlite"),
            PostingFetcher(cfg.fetch, root / "cache"), root / "out")


# ---- demo and cli -------------------------------------------------------------------------

def test_demo_runs_offline_and_prepares_the_expected_jobs(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("the demo must not use the network")

    monkeypatch.setattr(requests.Session, "request", no_network)
    summary, dashboard, queue = run_demo(loc_in(tmp_path))
    verdicts = {j.job_id: j.verdict for j in queue.list()}
    assert verdicts == {
        "3900000001": "fit", "3900000002": "reject", "3900000003": "reject", "3900000004": "reject",
        "3900000005": "borderline", "3900000006": "reject", "3900000007": "fit", "3900000008": "borderline",
    }
    assert len(summary.quarantined) == 2
    html = dashboard.read_text(encoding="utf-8")
    assert "Northwind Data Lab" in html and "Quarantined mails (2)" in html and "Nothing here was submitted" in html
    assert (dashboard.parent / "jobs" / "3900000001" / "cover_note.md").exists()


def test_cli_demo_list_mark_and_open(tmp_path, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    home = str(tmp_path)
    assert cli.main(["--home", home, "demo", "--no-open"]) == 0 and opened == []
    assert "Dashboard:" in capsys.readouterr().out
    # the demo lives in <home>/demo; point the normal commands at it
    demo_home = str(tmp_path / "demo")
    (tmp_path / "demo" / "config.yaml").write_text("mail:\n  provider: gmail\n", encoding="utf-8")
    assert cli.main(["--home", demo_home, "list", "--status", "prepared"]) == 0
    assert "3900000001" in capsys.readouterr().out
    assert cli.main(["--home", demo_home, "open", "3900000001"]) == 0
    assert opened == ["https://www.linkedin.com/jobs/view/3900000001"]
    assert cli.main(["--home", demo_home, "mark", "3900000001", "applied"]) == 0
    assert Queue(tmp_path / "demo" / "jac.sqlite").get("3900000001").status == "applied"
    assert cli.main(["--home", demo_home, "show", "123"]) == 2


def test_cli_init_import_and_profile_check(tmp_path, capsys):
    home = str(tmp_path)
    answers = tmp_path / "a.yaml"
    answers.write_text(yaml.safe_dump(ANSWERS), encoding="utf-8")
    assert cli.main(["--home", home, "init", "--answers", str(answers)]) == 0
    cv = tmp_path / "cv.html"
    cv.write_text("<html><title>Sam Rivers - CV</title><h2>Experience</h2><div class=\"entry\"><div class=\"row\"><div><span class=\"role\">Intern</span> · Acme</div><div class=\"date\">2025</div></div><ul><li>Did <b>3</b> things.</li></ul></div></html>", encoding="utf-8")
    assert cli.main(["--home", home, "import-cv", str(cv)]) == 0
    assert cli.main(["--home", home, "profile", "check"]) == 1  # gaps remain (no skills, etc.)
    out = capsys.readouterr().out
    assert "What was understood" in out and "What is missing" in out


def test_bundled_example_profile_matches_the_root_copy():
    root = Path(__file__).resolve().parent.parent
    assert (root / "profile.example.yaml").read_bytes() == (root / "src/jac/demo_data/profile.example.yaml").read_bytes()
