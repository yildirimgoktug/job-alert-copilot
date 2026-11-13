"""Regenerates the bundled demo mails in src/jac/demo_data/eml (run: python tests/make_demo_data.py)."""
from pathlib import Path

from mailfactory import GOOD_AUTH, build_mail, job_link

DEST = Path(__file__).resolve().parent.parent / "src" / "jac" / "demo_data" / "eml"


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    first = [job_link(f"390000000{i}") for i in (1, 2, 3, 4)]
    second = [job_link(f"390000000{i}") for i in (5, 6, 7, 8)] + ["https://lnkd.in/xYz123"]
    files = {
        "01-genuine-alert.eml": build_mail(first),
        "02-genuine-alert.eml": build_mail(second, subject="4 new jobs for 'student' in Amsterdam"),
        "03-spoofed-display-name.eml": build_mail(
            [job_link("3900000009")], sender="jobs-noreply@linkedin-careers.example", display="jobs-noreply@linkedin.com"
        ),
        "04-forged-auth-header.eml": build_mail(
            [job_link("3900000010")],
            auth=[GOOD_AUTH.replace("dkim=pass", "dkim=fail").replace("spf=pass", "spf=softfail"), GOOD_AUTH],
        ),
    }
    for name, raw in files.items():
        (DEST / name).write_bytes(raw)


if __name__ == "__main__":
    main()
