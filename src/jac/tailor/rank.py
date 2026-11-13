"""Rank facts and bullets for one posting.

The baseline is a plain keyword overlap and needs no LLM. The optional LLM re-rank may only
return ids that already exist in the fact bank, in a new order; unknown ids are dropped and logged.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from jac.llm import UNTRUSTED_NOTICE, LLMClient, LLMError, parse_json_reply, wrap_untrusted
from jac.postings import Posting
from jac.profile import Fact, Profile

log = logging.getLogger("jac.tailor")

_STOP = frozenset(
    "the and for with you your our are will have this that from about into who what which their they them can "
    "work team role job experience ability including such other more also not but all any new use using able "
    "intern internship student position company looking join".split()
)


@dataclass
class Selection:
    """Facts and bullets in order of relevance for one posting.

    fact_order lists every non-education fact id, best first. bullet_order maps a fact id to
    its bullet ids, best first. priority lists every bullet id, most important first; it decides
    what is dropped first when the CV has to shrink to one page.
    """

    fact_order: list[str]
    bullet_order: dict[str, list[str]]
    priority: list[str]
    dropped_ids: list[str] = field(default_factory=list)


def keywords(text: str) -> set[str]:
    """Lower-case content words (3+ characters, no stopwords) of a text."""
    return {w for w in re.findall(r"[a-z][a-z0-9+#]{2,}", text.lower()) if w not in _STOP}


def _bullet_score(words: set[str], fact: Fact, text: str) -> float:
    own = keywords(text.replace("**", ""))
    tags = keywords(" ".join(fact.tags))
    return len(own & words) + 1.5 * len(tags & words) + 0.5 * len(keywords(fact.title) & words)


def keyword_rank(profile: Profile, posting: Posting) -> Selection:
    """Baseline ranking by keyword overlap with the posting (ties keep the profile order)."""
    words = keywords(f"{posting.title} {posting.title} {posting.description}")
    scored: dict[str, list[tuple[float, int, str]]] = {}
    for fact in profile.facts:
        scored[fact.id] = sorted(
            ((-_bullet_score(words, fact, b.text), i, b.id) for i, b in enumerate(fact.bullets)),
        )
    bullet_order = {fid: [bid for _, _, bid in rows] for fid, rows in scored.items()}
    best = {fid: (rows[0][0] if rows else 0.0) for fid, rows in scored.items()}
    fact_score = {
        f.id: best[f.id] * 1.0 - 2.0 * len(keywords(" ".join(f.tags)) & words) for f in profile.facts
    }
    ranked = [f.id for f in sorted((f for f in profile.facts if f.kind != "education"),
                                   key=lambda f: (fact_score[f.id], profile.facts.index(f)))]
    all_bullets = sorted(
        ((row[0], fid, row[1], row[2]) for fid, rows in scored.items() for row in rows),
        key=lambda t: (t[0], ranked.index(t[1]) if t[1] in ranked else -1, t[2]),
    )
    return Selection(ranked, bullet_order, [bid for _, _, _, bid in all_bullets])


class RerankReply(BaseModel):
    """Strict schema: only ids, only orders."""

    model_config = ConfigDict(extra="forbid")

    fact_order: list[str]
    bullet_order: dict[str, list[str]]


RERANK_SYSTEM = (
    "You order the entries of a CV by relevance to a job posting. " + UNTRUSTED_NOTICE + " "
    'Reply with JSON {"fact_order": [fact ids, most relevant first], "bullet_order": {fact id: [bullet ids, '
    "most relevant first]}}. Use only ids from the CANDIDATE block. Never invent ids and never write any text."
)


def _candidate_block(profile: Profile) -> str:
    lines = []
    for fact in profile.facts:
        if fact.kind == "education":
            continue
        lines.append(f"{fact.id} | {fact.kind} | {fact.title} | tags: {', '.join(fact.tags)}")
        lines += [f"  {b.id} | {b.text.replace('**', '')}" for b in fact.bullets]
    return "\n".join(lines)


def llm_rerank(client: LLMClient, profile: Profile, posting: Posting, baseline: Selection) -> Selection:
    """Re-rank with an LLM. Unknown ids are dropped and logged; missing ids keep baseline order."""
    body = f"{posting.title}\n{posting.description}"
    user = f"CANDIDATE\n{_candidate_block(profile)}\n\nPOSTING\n{wrap_untrusted('POSTING', body)}"
    try:
        reply = parse_json_reply(client.complete(RERANK_SYSTEM, user), RerankReply)
    except LLMError as exc:
        log.warning("LLM re-rank skipped: %s", exc)
        return baseline
    assert isinstance(reply, RerankReply)
    dropped: list[str] = []

    def keep(ids: list[str], valid: list[str]) -> list[str]:
        good = [i for i in dict.fromkeys(ids) if i in valid]
        dropped.extend(i for i in ids if i not in valid)
        return good + [i for i in valid if i not in good]

    fact_order = keep(reply.fact_order, baseline.fact_order)
    bullet_order = {}
    for fid, base_ids in baseline.bullet_order.items():
        bullet_order[fid] = keep(reply.bullet_order.get(fid, []), base_ids)
    dropped.extend(f"{fid}/*" for fid in reply.bullet_order if fid not in baseline.bullet_order)
    if dropped:
        log.warning("LLM re-rank returned unknown ids, dropped: %s", sorted(set(dropped)))
    priority = [bid for fid in fact_order for bid in bullet_order[fid]]
    priority += [bid for fid, ids in bullet_order.items() if fid not in fact_order for bid in ids]
    return Selection(fact_order, bullet_order, priority, sorted(set(dropped)))
