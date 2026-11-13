"""Guards the product promise: nothing in the source can apply, log in, click or send mail."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "jac"

FORBIDDEN = {
    "browser automation": r"\b(?:import|from)\s+(?:selenium|playwright|pyautogui|pynput|mechanize|pyppeteer|splinter)\b",
    "mail sending": r"\bsmtplib\b|\.sendmail\(|\.send_message\(|\.messages\(\)\.send\(|\.drafts\(\)|\bsmtp\.",
    "gmail write scopes": r"gmail\.(?:send|compose|modify|insert|labels)|https://mail\.google\.com/",
    "linkedin login or cookies": r"linkedin\.com/(?:login|uas|checkpoint)|\bli_at\b|JSESSIONID|\bliap\b|cookies\s*=",
    "form posting": r"\.post\(.*linkedin|requests\.post\(",
}


def source_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py")]


def test_source_tree_is_not_empty():
    assert len(source_files()) > 10


def test_no_forbidden_code_in_the_source_tree():
    hits = []
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN.items():
            hits += [f"{path.name}: {label}: {m.group(0)}" for m in re.finditer(pattern, text)]
    assert not hits, hits


def test_http_post_is_only_used_for_the_local_llm():
    users = [p.name for p in source_files() if re.search(r"\.post\(", p.read_text(encoding="utf-8"))]
    assert users == ["llm.py"]


def test_gmail_scope_is_read_only():
    scopes = {s for p in source_files() for s in re.findall(r"googleapis\.com/auth/[\w.]+", p.read_text(encoding="utf-8"))}
    assert scopes <= {"googleapis.com/auth/gmail.readonly"}


def test_the_only_browser_actions_open_the_posting_url_or_a_local_file():
    text = "\n".join(p.read_text(encoding="utf-8") for p in source_files())
    arguments = re.findall(r"webbrowser\.open\((.*)\)", text)
    assert arguments
    assert all(a == "job.url" or a.endswith(".as_uri()") for a in arguments), arguments
