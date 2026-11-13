"""Posting pages: polite fetching of the public guest page and parsing into plain data.

Posting text is DATA. It is cleaned of hidden markup here, and nothing downstream may
treat it as instructions.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.cookiejar import DefaultCookiePolicy
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Comment, Tag
from pydantic import BaseModel

from jac.config import FetchConfig
from jac.sources import canonical_job_url

_BLOCK_TAGS = ["p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "ul", "ol"]
_WHITE = {"#fff", "#ffffff", "white", "rgb(255,255,255)", "transparent"}
_OFFSCREEN_PROPS = ("left", "top", "right", "text-indent", "margin-left", "margin-top")
_NUMBER = re.compile(r"^(-?\d*\.?\d+)")
_HIDDEN_CLASSES = {"sr-only", "visually-hidden", "hidden", "d-none"}
_STOP_STATUSES = {429, 999}
_WALL_MARKERS = ("authwall", "uas/login", "session_key", "join linkedin", "sign in to view")


class Posting(BaseModel):
    """A job posting reduced to the fields the rules need."""

    job_id: str
    url: str
    title: str
    company: str = ""
    location: str = ""
    employment_type: str = ""
    description: str
    date_posted: str = ""


def _declarations(style: str) -> dict[str, str]:
    """Inline CSS as {property: value}, lower-case, without spaces or !important."""
    decls: dict[str, str] = {}
    for part in style.split(";"):
        name, sep, value = part.partition(":")
        if sep:
            decls[name.strip().lower()] = re.sub(r"\s+|!important", "", value.lower())
    return decls


def _zero(value: str) -> bool:
    match = _NUMBER.match(value)
    return bool(match) and float(match.group(1)) == 0


def _style_hides(style: str) -> bool:
    """True for the usual ways of hiding text with an inline style.

    Each property is checked on its own, so 'background-color:#fff' is not mistaken for
    white text. Rules in <style> sheets are not evaluated; see the README's limitations.
    """
    d = _declarations(style)
    clipped = d.get("clip", "").startswith("rect(0") or d.get("clip-path", "") in {"inset(50%)", "circle(0)"}
    tiny = any(_zero(d.get(p, "")) for p in ("height", "max-height", "width", "max-width"))
    offscreen = any(
        (m := _NUMBER.match(d.get(p, ""))) and float(m.group(1)) <= -999 for p in _OFFSCREEN_PROPS
    )
    return bool(
        d.get("display") == "none"
        or d.get("visibility") in {"hidden", "collapse"}
        or _zero(d.get("font-size", "x"))
        or _zero(d.get("opacity", "x"))
        or d.get("color") in _WHITE
        or clipped
        or offscreen
        or "scale(0" in d.get("transform", "")
        or (tiny and d.get("overflow") == "hidden")
    )


def _is_hidden(tag: Tag) -> bool:
    attrs = tag.attrs or {}
    if "hidden" in attrs or str(attrs.get("aria-hidden", "")).lower() == "true":
        return True
    if _style_hides(str(attrs.get("style", ""))):
        return True
    return bool(_HIDDEN_CLASSES & {c.lower() for c in attrs.get("class", [])})


def html_to_text(html: str) -> str:
    """Visible text of an HTML fragment: hidden elements, scripts and comments are dropped."""
    soup = BeautifulSoup(html, "html.parser")
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in soup.find_all(["script", "style", "template", "noscript", "iframe", "svg"]):
        tag.decompose()
    for tag in soup.find_all(True):
        if not getattr(tag, "decomposed", False) and _is_hidden(tag):
            tag.decompose()
    for tag in soup.find_all("li"):
        tag.insert(0, "• ")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.append("\n")
    text = soup.get_text("")
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _find_job_posting(node: object) -> dict | None:
    if isinstance(node, list):
        return next((found for item in node if (found := _find_job_posting(item))), None)
    if isinstance(node, dict):
        types = node.get("@type")
        if types == "JobPosting" or (isinstance(types, list) and "JobPosting" in types):
            return node
        return _find_job_posting(node.get("@graph"))
    return None


def _ld_location(data: dict) -> str:
    locations = data.get("jobLocation") or []
    locations = locations if isinstance(locations, list) else [locations]
    parts: list[str] = []
    for location in locations:
        address = (location or {}).get("address") or {}
        if isinstance(address, str):
            parts.append(address)
            continue
        country = address.get("addressCountry", "")
        country = country.get("name", "") if isinstance(country, dict) else country
        parts.append(", ".join(p for p in (address.get("addressLocality"), address.get("addressRegion"), country) if p))
    if str(data.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        parts.append("Remote")
    return "; ".join(p for p in parts if p)


def _parse_json_ld(soup: BeautifulSoup, job_id: str, url: str) -> Posting | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _find_job_posting(json.loads(script.string or "", strict=False))
        except ValueError:
            continue
        if not data or not data.get("title") or not data.get("description"):
            continue
        employment = data.get("employmentType", "")
        employment = ", ".join(employment) if isinstance(employment, list) else str(employment)
        org = data.get("hiringOrganization") or {}
        return Posting(
            job_id=job_id,
            url=url,
            title=html_to_text(str(data["title"])),
            company=str(org.get("name", "")) if isinstance(org, dict) else str(org),
            location=_ld_location(data),
            employment_type=employment,
            description=html_to_text(str(data["description"])),
            date_posted=str(data.get("datePosted", "")),
        )
    return None


def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node and (text := html_to_text(str(node)).replace("\n", " ").strip()):
            return text
    return ""


def _parse_html_fallback(soup: BeautifulSoup, job_id: str, url: str) -> Posting | None:
    title = _first_text(soup, ["h1.top-card-layout__title", "h1.topcard__title", "h1"])
    description = ""
    for selector in [".show-more-less-html__markup", ".description__text", "[class*=job-description]", "main", "article"]:
        node = soup.select_one(selector)
        if node is not None:
            description = html_to_text(str(node))
            break
    if not title or len(description) < 80:
        return None
    return Posting(
        job_id=job_id,
        url=url,
        title=title,
        company=_first_text(soup, [".topcard__org-name-link", "[class*=org-name]", "[class*=company]"]),
        location=_first_text(soup, [".topcard__flavor--bullet", "[class*=location]"]),
        employment_type=_first_text(soup, ["[class*=employment-type]", "[class*=job-criteria]"]),
        description=description,
    )


def parse_posting(html: str, job_id: str) -> Posting | None:
    """Parse a posting page: JSON-LD JobPosting first, visible HTML as a fallback."""
    url = f"https://www.linkedin.com/jobs/view/{job_id}"
    soup = BeautifulSoup(html, "html.parser")
    return _parse_json_ld(soup, job_id, url) or _parse_html_fallback(soup, job_id, url)


def looks_like_login_wall(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in _WALL_MARKERS)


@dataclass(frozen=True)
class FetchResult:
    """Outcome of asking for one posting page. html is set only for 'ok' and 'cache'."""

    status: str
    html: str | None = None
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.html is not None


def _cookieless_session() -> requests.Session:
    """A session whose cookie jar refuses every cookie, so none is ever stored or sent back."""
    session = requests.Session()
    session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    return session


class PostingFetcher:
    """Fetches public guest posting pages, one request per interval, with a per-run cap.

    Only plain GET requests with a descriptive User-Agent. No cookies, no login, no redirects
    off the posting itself. A 429/999 or a login wall stops all further requests in this run.
    """

    def __init__(
        self,
        cfg: FetchConfig,
        cache_dir: Path,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cfg = cfg
        self._cache = cache_dir / "postings"
        self._stamp = cache_dir / "last_request.txt"
        self._session = session or _cookieless_session()
        self._clock = clock
        self._sleep = sleep
        self._requests_made = 0
        self.halted_reason = ""

    def fetch(self, job_id: str) -> FetchResult:
        cached = self._cache / f"{job_id}.html"
        if cached.exists():
            return FetchResult("cache", cached.read_text(encoding="utf-8"))
        if self._cfg.offline:
            return FetchResult("offline", reason="offline mode and not in the cache")
        if self.halted_reason:
            return FetchResult("halted", reason=self.halted_reason)
        if self._requests_made >= self._cfg.max_requests_per_run:
            return FetchResult("cap_reached", reason=f"per-run cap of {self._cfg.max_requests_per_run} requests reached")
        result = self._download(job_id)
        if result.status == "ok":
            self._cache.mkdir(parents=True, exist_ok=True)
            cached.write_text(result.html or "", encoding="utf-8")
        return result

    def _wait_for_slot(self) -> None:
        try:
            last = float(self._stamp.read_text())
        except (OSError, ValueError):
            last = 0.0
        remaining = self._cfg.min_interval_seconds - (self._clock() - last)
        if remaining > 0:
            self._sleep(remaining)

    def _get(self, url: str) -> requests.Response:
        self._wait_for_slot()
        self._requests_made += 1
        self._stamp.parent.mkdir(parents=True, exist_ok=True)
        self._stamp.write_text(str(self._clock()))
        return self._session.get(
            url,
            headers={"User-Agent": self._cfg.user_agent, "Accept": "text/html"},
            timeout=self._cfg.timeout_seconds,
            allow_redirects=False,
        )

    def _halt(self, reason: str) -> FetchResult:
        self.halted_reason = reason
        return FetchResult("blocked", reason=f"{reason}; open it manually")

    def _download(self, job_id: str) -> FetchResult:
        url = f"https://www.linkedin.com/jobs/view/{job_id}"
        for _ in range(3):
            try:
                response = self._get(url)
            except requests.RequestException as exc:
                return FetchResult("error", reason=f"request failed: {type(exc).__name__}")
            if response.status_code in _STOP_STATUSES:
                return self._halt(f"HTTP {response.status_code} (rate limited); stopping for this run")
            if 300 <= response.status_code < 400:
                location = urljoin(url, response.headers.get("Location", ""))
                target = canonical_job_url(location)
                if target is None or target.rsplit("/", 1)[1] != job_id:
                    return self._halt("redirected away from the posting (login wall); stopping for this run")
                url = location  # same host, same job: follow exactly what was given
                continue
            if response.status_code != 200:
                return FetchResult("error", reason=f"HTTP {response.status_code}")
            if parse_posting(response.text, job_id) is None:
                if looks_like_login_wall(response.text):
                    return self._halt("login wall; stopping for this run")
                return FetchResult("error", reason="page could not be parsed as a job posting")
            return FetchResult("ok", response.text)
        return self._halt("too many redirects; stopping for this run")

