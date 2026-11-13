"""Mail authenticity and link safety. This runs before anything else touches a message.

A message is accepted only if every check passes; otherwise it is quarantined with the
reasons and its body is never parsed for links. Display names are never trusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.message import EmailMessage, Message
from email.utils import getaddresses
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from jac.config import MailConfig

_JOB_PATH_RE = re.compile(r"^/(?:comm/)?jobs/view/(\d{4,20})/?$")
_ALLOWED_HOSTS = {"www.linkedin.com", "linkedin.com"}
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.IGNORECASE)
_COMMENT_RE = re.compile(r"\([^)]*\)")
_RESULT_RE = re.compile(r"^(dkim|spf|dmarc)=([a-z]+)\b(.*)$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class MailVerdict:
    """Outcome of verifying one message."""

    accepted: bool
    reasons: list[str]
    sender: str = ""
    job_urls: list[str] = field(default_factory=list)
    rejected_links: list[str] = field(default_factory=list)


def _domain(address: str) -> str:
    return address.rpartition("@")[2].strip().lower()


def _parse_auth_results(header: str) -> tuple[str, dict[str, list[tuple[str, dict[str, str]]]]]:
    """Split one Authentication-Results header into (authserv-id, {method: [(result, props)]})."""
    segments = [s.strip() for s in _COMMENT_RE.sub(" ", header).split(";")]
    authserv_id = segments[0].split()[0].lower() if segments and segments[0] else ""
    results: dict[str, list[tuple[str, dict[str, str]]]] = {}
    for segment in segments[1:]:
        match = _RESULT_RE.match(segment.strip())
        if not match:
            continue
        method, result, rest = match.group(1).lower(), match.group(2).lower(), match.group(3)
        props = {k.lower(): v for k, v in re.findall(r"([A-Za-z]+\.[A-Za-z-]+)=(\S+)", rest)}
        results.setdefault(method, []).append((result, props))
    return authserv_id, results


def _prop_domain(value: str) -> str:
    return _domain(value.strip("<>")) if "@" in value else value.strip("<>").lower()


def _check_auth(msg: Message, cfg: MailConfig) -> list[str]:
    """Check the receiving server's verdicts. Only the topmost Authentication-Results counts.

    The receiving server prepends its own header, so the top one is genuine and any header
    below it may have been forged by the sender. If the top header is not from a trusted
    authserv-id we refuse the message rather than search downwards for a better-looking one.
    """
    headers = msg.get_all("Authentication-Results") or []
    if not headers:
        return ["no Authentication-Results header"]
    authserv_id, results = _parse_auth_results(str(headers[0]))
    trusted = {t.lower() for t in cfg.trusted_authserv_ids}
    if authserv_id not in trusted:
        return [f"topmost Authentication-Results is from {authserv_id or 'unknown'}, not a trusted receiver"]
    reasons: list[str] = []
    domain = cfg.sender_domain
    dkim_ok = any(
        result == "pass"
        and domain in (_prop_domain(props.get("header.d", "")), _prop_domain(props.get("header.i", "")))
        for result, props in results.get("dkim", [])
    )
    if not dkim_ok:
        reasons.append(f"no dkim=pass signed by {domain}")
    if not any(result == "pass" for result, _ in results.get("spf", [])):
        reasons.append("spf is not pass")
    dmarc = results.get("dmarc", [])
    if not any(result == "pass" for result, _ in dmarc):
        reasons.append("dmarc is not pass")
    for result, props in dmarc:
        header_from = props.get("header.from")
        if result == "pass" and header_from and _prop_domain(header_from) != domain:
            reasons.append(f"dmarc header.from is {header_from}")
    return reasons


def _check_headers(msg: Message, cfg: MailConfig) -> tuple[str, list[str]]:
    reasons: list[str] = []
    from_headers = msg.get_all("From") or []
    addresses = [a for _, a in getaddresses([str(h) for h in from_headers]) if a]
    if len(addresses) != 1:
        return "", [f"expected exactly one From address, found {len(addresses)}"]
    sender = addresses[0].lower()
    if _domain(sender) != cfg.sender_domain:
        reasons.append(f"From domain {_domain(sender)!r} is not exactly {cfg.sender_domain}")
    if sender not in {s.lower() for s in cfg.allowed_senders}:
        reasons.append(f"sender {sender} is not in the allowed sender list")
    reply_to = [a for _, a in getaddresses([str(h) for h in msg.get_all("Reply-To") or []]) if a]
    for address in reply_to:
        if _domain(address) != cfg.sender_domain:
            reasons.append(f"Reply-To {address} is not at {cfg.sender_domain}")
    return sender, reasons


def canonical_job_url(url: str) -> str | None:
    """Return https://www.linkedin.com/jobs/view/<id> for a safe job-view link, else None.

    Rejects anything that is not https, any host other than linkedin.com or www.linkedin.com
    (so shorteners, lookalikes, punycode and userinfo tricks all fail), ports, and every path
    except a numeric job view. All query strings and fragments are dropped.
    """
    url = url.strip()
    if any(ch.isspace() or ord(ch) < 32 or ch == "\\" for ch in url):
        return None
    try:
        parts = urlsplit(url)
        host_ok = parts.netloc.lower() in _ALLOWED_HOSTS
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not host_ok:
        return None
    match = _JOB_PATH_RE.match(parts.path)
    if not match:
        return None
    return f"https://www.linkedin.com/jobs/view/{match.group(1)}"


def job_id_from_url(canonical: str) -> str:
    return canonical.rstrip("/").rsplit("/", 1)[1]


def _candidate_urls(msg: Message) -> list[str]:
    """URLs from the text and HTML bodies. Attachments are skipped and never opened."""
    urls: list[str] = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/html":
            urls += [a["href"] for a in BeautifulSoup(text, "html.parser").find_all("a", href=True)]
        urls += _URL_IN_TEXT_RE.findall(text)
    return urls


def verify_message(msg: EmailMessage | Message, cfg: MailConfig) -> MailVerdict:
    """Verify one message. Links are only extracted from accepted messages."""
    sender, reasons = _check_headers(msg, cfg)
    reasons += _check_auth(msg, cfg)
    if reasons:
        return MailVerdict(accepted=False, reasons=reasons, sender=sender)
    job_urls: list[str] = []
    rejected: list[str] = []
    for url in _candidate_urls(msg):
        canonical = canonical_job_url(url)
        if canonical is None:
            rejected.append(url)
        elif canonical not in job_urls:
            job_urls.append(canonical)
    return MailVerdict(True, [], sender, job_urls, rejected)
