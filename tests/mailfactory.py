"""Builds synthetic alert e-mails for tests and for the committed examples/eml fixtures."""
from __future__ import annotations

from email.message import EmailMessage

GOOD_AUTH = (
    "mx.google.com; "
    "dkim=pass header.i=@linkedin.com header.s=proddkim1024 header.b=AbCdEf12; "
    "spf=pass (google.com: domain of jobalerts-noreply@linkedin.com designates 108.174.3.1 "
    "as permitted sender) smtp.mailfrom=jobalerts-noreply@linkedin.com; "
    "dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=linkedin.com"
)


def job_link(job_id: str, query: str = "?trackingId=abc%3D%3D&refId=xyz&midToken=secret") -> str:
    return f"https://www.linkedin.com/comm/jobs/view/{job_id}/{query}"


def build_mail(
    links: list[str] | None = None,
    sender: str = "jobalerts-noreply@linkedin.com",
    display: str = "LinkedIn Job Alerts",
    auth: list[str] | None = None,
    reply_to: str | None = None,
    subject: str = "3 new jobs for 'AI intern' in Amsterdam",
    extra_headers: list[tuple[str, str]] | None = None,
) -> bytes:
    """Return RFC 822 bytes. auth lists Authentication-Results values, topmost first."""
    links = links if links is not None else [job_link("3901234567")]
    msg = EmailMessage()
    for value in auth if auth is not None else [GOOD_AUTH]:
        msg["Authentication-Results"] = value
    msg["From"] = f'"{display}" <{sender}>'
    msg["To"] = "student@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Tue, 10 Nov 2026 08:00:00 +0000"
    if reply_to:
        msg["Reply-To"] = reply_to
    for name, value in extra_headers or []:
        msg[name] = value
    items = "".join(f'<li><a href="{url}">Job {i}</a></li>' for i, url in enumerate(links, 1))
    msg.set_content("\n".join(links))
    msg.add_alternative(f"<html><body><p>Jobs for you</p><ul>{items}</ul></body></html>", subtype="html")
    return msg.as_bytes()
