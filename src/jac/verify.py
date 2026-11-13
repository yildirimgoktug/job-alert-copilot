"""Mail authenticity and link safety. This runs before anything else touches a message.

A message is accepted only if every check passes; otherwise it is quarantined with the
reasons and its body is never parsed for links. Display names are never trusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.message import Message
from email.utils import getaddresses

from bs4 import BeautifulSoup

from jac.config import ConfigError
from jac.sources import Source

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


def authserv_trusted(authserv_id: str, trusted: list[str]) -> bool:
    """Exact match, or a '*.suffix' entry for providers whose server names vary."""
    for entry in (t.lower() for t in trusted):
        if entry.startswith("*."):
            if authserv_id.endswith(entry[1:]):
                return True
        elif authserv_id == entry:
            return True
    return False


def _check_auth(msg: Message, source: Source, trusted: list[str]) -> list[str]:
    """Check the receiving server's verdicts. Only the topmost Authentication-Results counts.

    The receiving server prepends its own header, so the top one is genuine and any header
    below it may have been forged by the sender. If the top header is not from a trusted
    authserv-id we refuse the message rather than search downwards for a better-looking one.
    """
    headers = msg.get_all("Authentication-Results") or []
    if not headers:
        return ["no Authentication-Results header"]
    authserv_id, results = _parse_auth_results(str(headers[0]))
    if not authserv_trusted(authserv_id, trusted):
        return [f"topmost Authentication-Results is from {authserv_id or 'unknown'}, not a trusted receiver"]
    reasons: list[str] = []
    domain = source.sender_domain
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


def _check_headers(msg: Message, source: Source) -> tuple[str, list[str]]:
    reasons: list[str] = []
    addresses = [a for _, a in getaddresses([str(h) for h in msg.get_all("From") or []]) if a]
    if len(addresses) != 1:
        return "", [f"expected exactly one From address, found {len(addresses)}"]
    sender = addresses[0].lower()
    if _domain(sender) != source.sender_domain:
        reasons.append(f"From domain {_domain(sender)!r} is not exactly {source.sender_domain}")
    if sender not in {s.lower() for s in source.allowed_senders}:
        reasons.append(f"sender {sender} is not in the allowed sender list")
    for _, address in getaddresses([str(h) for h in msg.get_all("Reply-To") or []]):
        if address and _domain(address) != source.sender_domain:
            reasons.append(f"Reply-To {address} is not at {source.sender_domain}")
    return sender, reasons


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


def verify_message(msg: Message, source: Source, trusted_authserv_ids: list[str]) -> MailVerdict:
    """Verify one message for a source. Links are only extracted from accepted messages.

    Raises ConfigError when no trusted authserv-id is configured: without it we cannot tell
    the receiving server's header from a forged one, so we refuse to verify at all.
    """
    if not trusted_authserv_ids:
        raise ConfigError(
            "mail.trusted_authserv_ids is not set. Set mail.provider (gmail, outlook, yahoo, fastmail) "
            "or list the authserv-id of your mail provider; verification will not run without it."
        )
    sender, reasons = _check_headers(msg, source)
    reasons += _check_auth(msg, source, trusted_authserv_ids)
    if reasons:
        return MailVerdict(accepted=False, reasons=reasons, sender=sender)
    job_urls: list[str] = []
    rejected: list[str] = []
    for url in _candidate_urls(msg):
        canonical = source.canonicalize(url)
        if canonical is None:
            rejected.append(url)
        elif canonical not in job_urls:
            job_urls.append(canonical)
    return MailVerdict(True, [], sender, job_urls, rejected)
