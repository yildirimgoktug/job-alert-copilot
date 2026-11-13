"""Parse a one-page HTML CV into a draft profile.

Expected shape: an h1 or title with the name, h2 section headings, entries as div.entry or
div.proj with a role/date row, li bullets, and an optional skills table. Deterministic; no LLM.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from jac.importers.draft import IdFactory, make_fact, split_sentences
from jac.profile import Fact, FactKind, Identity, Profile, SkillGroup

_SECTION_KIND = {"education": "education", "experience": "experience", "work": "experience",
                 "project": "project", "publication": "publication"}
_PHONE = re.compile(r"\+?\d[\d \-()]{7,}\d")


def _text(node: Tag | None) -> str:
    return " ".join(node.get_text().split()) if node else ""


def _identity(soup: BeautifulSoup) -> Identity:
    title = _text(soup.find("title"))
    name = re.split(r"\s[—–-]\s", title)[0].strip() if title else _text(soup.find("h1")).title()
    header = soup.find("header") or soup
    contact = header.select_one(".contact")
    contact_text = contact.get_text("\n", strip=True) if contact else ""
    email = next((a["href"][7:] for a in header.find_all("a", href=True) if a["href"].startswith("mailto:")), "")
    phone_match = _PHONE.search(contact_text)
    first_line = contact_text.splitlines()[0] if contact_text else ""
    location = first_line.split("·")[0].strip() if "·" in first_line else ""
    links: dict[str, str] = {}
    for anchor in header.find_all("a", href=True):
        host = (urlsplit(anchor["href"]).hostname or "").removeprefix("www.")
        if host:
            links.setdefault(host.split(".")[0], anchor["href"])
    return Identity(
        name=name,
        headline=_text(header.select_one(".tagline")),
        email=email,
        phone=phone_match.group(0).strip() if phone_match else "",
        location=location,
        links=links,
    )


def _section_nodes(h2: Tag) -> list[Tag]:
    nodes: list[Tag] = []
    for sibling in h2.next_siblings:
        if isinstance(sibling, Tag):
            if sibling.name == "h2":
                break
            nodes.append(sibling)
    return nodes


def _entry_fact(ids: IdFactory, kind: FactKind, node: Tag) -> Fact:
    row = node.select_one(".row")
    role = _text(node.select_one(".role"))
    head_div = row.find("div") if row else None
    head = _text(head_div) if head_div else role
    org = head.removeprefix(role).strip(" ·,")
    bullets = [_text(li) for li in node.find_all("li")]
    if not bullets:
        bullets = [_text(d) for d in node.find_all("div", recursive=False) if "row" not in (d.get("class") or [])]
    link = next((a["href"] for a in node.find_all("a", href=True) if not a["href"].startswith("mailto:")), "")
    return make_fact(ids, kind, role or head, bullets, org=org, dates=_text(node.select_one(".date")),
                     summary=_text(node.select_one(".sub")), link=link)


def _project_fact(ids: IdFactory, node: Tag) -> Fact:
    role = _text(node.select_one(".role"))
    meta = _text(node.select_one(".meta")).lstrip("· ").strip()
    link = next((a["href"] for a in node.find_all("a", href=True)), "")
    parts = [p.strip() for p in meta.split("·") if p.strip() and "github.com" not in p]
    team = next((p for p in parts if re.match(r"(individual|team of)", p, re.I)), "")
    tags = [t.strip() for p in parts if p != team for t in p.split(",") if t.strip()]
    clone = BeautifulSoup(str(node), "html.parser")
    for part in clone.select(".role, .meta"):
        part.decompose()
    return make_fact(ids, "project", role, split_sentences(_text(clone)), team=team, link=link, tags=tags)


def _publication_fact(ids: IdFactory, node: Tag) -> Fact:
    title = _text(node.find("i")) or _text(node)[:80]
    link = next((a["href"] for a in node.find_all("a", href=True)), "")
    return make_fact(ids, "publication", title, [_text(node)], link=link)


def _skills(node: Tag) -> list[SkillGroup]:
    groups = []
    for row in node.find_all("tr"):
        cells = [_text(td) for td in row.find_all("td")]
        if len(cells) == 2:
            groups.append(SkillGroup(name=cells[0], items=[i.strip() for i in cells[1].split(",") if i.strip()]))
    return groups


def parse_cv_html(html: str) -> Profile:
    """Turn the CV HTML into a draft Profile (facts get generated ids)."""
    soup = BeautifulSoup(html, "html.parser")
    ids = IdFactory()
    facts: list[Fact] = []
    skills: list[SkillGroup] = []
    summary = ""
    for h2 in soup.find_all("h2"):
        heading = _text(h2).lower()
        nodes = _section_nodes(h2)
        kind = next((k for key, k in _SECTION_KIND.items() if key in heading), None)
        if heading.startswith(("profile", "summary", "about")):
            summary = " ".join(_text(n) for n in nodes if n.name == "p")
        elif heading.startswith("skill"):
            skills = [g for n in nodes for g in _skills(n)]
        elif kind in ("education", "experience"):
            facts += [_entry_fact(ids, kind, n) for n in nodes if "entry" in (n.get("class") or [])]
        elif kind == "project":
            facts += [_project_fact(ids, n) for n in nodes if "proj" in (n.get("class") or [])]
        elif kind == "publication":
            facts += [_publication_fact(ids, n) for n in nodes if "proj" in (n.get("class") or [])]
    return Profile(identity=_identity(soup), summary=summary, facts=facts, skills=skills)
