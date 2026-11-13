"""Mail authenticity and link safety. This runs before anything else touches a message.

A message is accepted only if every check passes; otherwise it is quarantined with the
reasons and its body is never parsed for links. Display names are never trusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import errors
from email.message import Message
from email.utils import getaddresses

from bs4 import BeautifulSoup

from jac.config import ConfigError
from jac.sources import Source

_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.IGNORECASE)
_RESULT_RE = re.compile(r"^(dkim|spf|dmarc)=([a-z]+)\b(.*)$", re.IGNORECASE | re.DOTALL)
_PROP_RE = re.compile(r'([A-Za-z]+\.[A-Za-z-]+)=("(?:[^"\\]|\\.)*"|[^\s";]+)')
# Header lines Python skips but another parser may read; such a message is not trusted.
_HEADER_DEFECTS = (errors.MisplacedEnvelopeHeaderDefect, errors.MissingHeaderBodySeparatorDefect)


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


def _raw_values(msg: Message, name: str) -> list[str]:
    """Header values exactly as received (encoded words not decoded), unfolded, in order."""
    return [re.sub(r"\r?\n[ \t]", " ", str(v)) for k, v in msg.raw_items() if k.lower() == name.lower()]


def _split_segments(header: str) -> list[str]:
    """Split on ';' outside quoted strings, dropping (nested) comments.

    Receivers echo sender-controlled text (the envelope sender) in comments and quoted
    strings. A naive split would let a ')' or ';' inside that text start a forged result,
    so a quoted string is honoured even inside a comment; an unbalanced quote swallows the
    rest of the header, which fails closed.
    """
    segments, current, depth, quoted, escaped = [], [], 0, False, False
    for ch in header:
        if escaped:
            escaped = False
            if depth == 0:
                current.append(ch)
            continue
        if ch == "\\" and (quoted or depth):
            escaped = True
            if depth == 0:
                current.append(ch)
            continue
        if quoted:
            quoted = ch != '"'
            if depth == 0:
                current.append(ch)
        elif depth:
            quoted = ch == '"'
            depth += {"(": 1, ")": -1}.get(ch, 0)
            if depth == 0:
                current.append(" ")
        elif ch == "(":
            depth = 1
        elif ch == '"':
            quoted = True
            current.append(ch)
        elif ch == ";":
            segments.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    segments.append("".join(current).strip())
    return segments


def _parse_auth_results(header: str) -> tuple[str, dict[str, list[tuple[str, dict[str, str]]]]]:
    """Split one Authentication-Results header into (authserv-id, {method: [(result, props)]})."""
    segments = _split_segments(header)
    authserv_id = segments[0].split()[0].lower() if segments and segments[0].split() else ""
    results: dict[str, list[tuple[str, dict[str, str]]]] = {}
    for segment in segments[1:]:
        match = _RESULT_RE.match(segment)
        if not match:
            continue
        method, result, rest = match.group(1).lower(), match.group(2).lower(), match.group(3)
        props = {k.lower(): v for k, v in _PROP_RE.findall(rest)}
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
    headers = _raw_values(msg, "Authentication-Results")
    if not headers:
        return ["no Authentication-Results header"]
    authserv_id, results = _parse_auth_results(headers[0])
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
    spf = results.get("spf", [])
    if not spf or any(result != "pass" for result, _ in spf):
        reasons.append("spf is not pass")
    dmarc = results.get("dmarc", [])
    if not dmarc or any(result != "pass" for result, _ in dmarc):
        reasons.append("dmarc is not pass")
    for _, props in dmarc:
        header_from = props.get("header.from", "")
        if _prop_domain(header_from) != domain:
            reasons.append(f"dmarc header.from is {header_from or 'missing'}, not {domain}")
    return reasons


def _addresses(msg: Message, name: str) -> list[str]:
    """Addresses parsed from the raw header values, so encoded words cannot become addresses."""
    return [a for _, a in getaddresses(_raw_values(msg, name)) if a]


def _check_headers(msg: Message, source: Source) -> tuple[str, list[str]]:
    if any(isinstance(d, _HEADER_DEFECTS) for d in msg.defects):
        return "", ["malformed header block (a header line the parser could not read)"]
    reasons: list[str] = []
    raw_from = _raw_values(msg, "From")
    addresses = _addresses(msg, "From")
    if len(raw_from) != 1 or len(addresses) != 1:
        return "", [f"expected exactly one From address, found {len(addresses)}"]
    sender = addresses[0].lower()
    if raw_from[0].count("@") != 1:
        reasons.append("From header holds a second address in its display name or a comment")
    if _domain(sender) != source.sender_domain:
        reasons.append(f"From domain {_domain(sender)!r} is not exactly {source.sender_domain}")
    if sender not in {s.lower() for s in source.allowed_senders}:
        reasons.append(f"sender {sender} is not in the allowed sender list")
    for header in ("Sender", "Reply-To"):
        for address in _addresses(msg, header):
            if _domain(address) != source.sender_domain:
                reasons.append(f"{header} {address} is not at {source.sender_domain}")
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
