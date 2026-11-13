"""Alert sources. Each source states who may send its alerts and which links are job links.

LinkedIn is the first source. To add another (Indeed, Glassdoor, ...) define a Source with
its own sender domain, allowed sender addresses and URL rules, and register it in SOURCES.
Verification and link extraction are generic and use only what the Source declares.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Source:
    """What makes a mail and a link genuine for one job site."""

    name: str
    sender_domain: str
    allowed_senders: tuple[str, ...]
    canonicalize: Callable[[str], str | None]
    posting_page: Callable[[str], str]

    def job_id(self, canonical_url: str) -> str:
        return canonical_url.rstrip("/").rsplit("/", 1)[1]


_LINKEDIN_PATH = re.compile(r"^/(?:comm/)?jobs/view/([0-9]{4,20})/?$")  # ASCII digits only
_LINKEDIN_HOSTS = {"www.linkedin.com", "linkedin.com"}


def canonical_job_url(url: str) -> str | None:
    """Return https://www.linkedin.com/jobs/view/<id> for a safe LinkedIn job link, else None.

    Rejects anything that is not https, any host other than linkedin.com or www.linkedin.com
    (so shorteners, lookalikes, punycode and userinfo tricks all fail), ports, and every path
    except a numeric job view. All query strings and fragments are dropped.
    """
    url = url.strip()
    if any(ch.isspace() or ord(ch) < 32 or ch == "\\" for ch in url):
        return None
    try:
        parts = urlsplit(url)
        host_ok = parts.netloc.lower() in _LINKEDIN_HOSTS
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not host_ok:
        return None
    match = _LINKEDIN_PATH.match(parts.path)
    return f"https://www.linkedin.com/jobs/view/{match.group(1)}" if match else None


LINKEDIN = Source(
    name="linkedin",
    sender_domain="linkedin.com",
    allowed_senders=("jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com", "jobs-listings@linkedin.com"),
    canonicalize=canonical_job_url,
    posting_page=lambda job_id: f"https://www.linkedin.com/jobs/view/{job_id}",
)

SOURCES: dict[str, Source] = {LINKEDIN.name: LINKEDIN}
