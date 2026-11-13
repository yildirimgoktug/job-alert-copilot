import json
import logging
import re

from jac.llm import FakeLLM
from jac.postings import html_to_text
from jac.profile import Profile
from jac.tailor.build import build_application
from jac.tailor.cover import write_cover_note
from jac.tailor.rank import keyword_rank, llm_rerank
from jac.tailor.render import HeuristicPageCounter, render_cv
from jac.tailor.validator import validate_text


def note(text: str) -> str:
    return json.dumps({"note": text})


GOOD = (
    "Dear hiring team at Northwind Data Lab, I am a student of Artificial Intelligence and I built a local "
    "RAG search over 2,000 course descriptions. I would enjoy working on retrieval with your team in Amsterdam."
)


def test_note_built_from_the_fact_bank_passes(profile, load_posting):
    assert validate_text(GOOD, profile, load_posting("3900000001")).ok


def test_number_not_in_bank_or_posting_is_rejected(profile, load_posting):
    result = validate_text(GOOD.replace("2,000", "5,000"), profile, load_posting("3900000001"))
    assert any("5000" in v for v in result.violations)


def test_percentage_and_year_not_in_bank_are_rejected(profile, load_posting):
    posting = load_posting("3900000001")
    assert not validate_text("I improved accuracy by 93% in 2019.", profile, posting).ok
    assert validate_text("Accuracy rose from 41% to 72%.", profile, posting).ok  # both are in the bank


def test_number_words_are_checked(profile, load_posting):
    result = validate_text("I have five years of experience.", profile, load_posting("3900000001"))
    assert any("five" in v for v in result.violations)


def test_posting_numbers_are_allowed(profile, load_posting):
    assert validate_text("I can work 16 hours per week.", profile, load_posting("3900000001")).ok


def test_unknown_company_and_technology_are_rejected(profile, load_posting):
    posting = load_posting("3900000001")
    company = validate_text("I worked at Globex Corporation on retrieval.", profile, posting)
    assert any("Globex" in v for v in company.violations)
    tech = validate_text("I used Spark and Docker in a project.", profile, posting)
    assert {"spark", "docker"} <= {v.split(": ")[1].lower() for v in tech.violations}


def test_technology_in_the_posting_alone_is_allowed(profile, load_posting):
    assert validate_text("I know pytest well.", profile, load_posting("3900000001")).ok


def test_never_claim_strings_are_rejected(profile, load_posting):
    result = validate_text("I like blockchain and rust.", profile, load_posting("3900000001"))
    assert "never_claim string present: blockchain" in result.violations


def test_llm_note_that_passes_is_used(profile, load_posting):
    posting = load_posting("3900000001")
    result = write_cover_note(profile, posting, keyword_rank(profile, posting), FakeLLM([note(GOOD)]))
    assert result.source == "llm" and result.attempts == 1 and result.text == GOOD


def test_rejected_note_is_retried_once_then_accepted(profile, load_posting):
    posting = load_posting("3900000001")
    llm = FakeLLM([note(GOOD.replace("2,000", "9,999")), note(GOOD)])
    result = write_cover_note(profile, posting, keyword_rank(profile, posting), llm)
    assert result.source == "llm" and result.attempts == 2
    assert "9999" in " ".join(result.rejections[0])
    assert "9999" in llm.calls[1][1]  # the retry is told why


def test_two_rejections_fall_back_to_the_template(profile, load_posting):
    posting = load_posting("3900000001")
    bad = note(GOOD.replace("2,000", "9,999"))
    result = write_cover_note(profile, posting, keyword_rank(profile, posting), FakeLLM([bad, bad]))
    assert result.source == "template" and result.attempts == 2
    assert validate_text(result.text, profile, posting).ok
    assert "Northwind Data Lab" in result.text and "Alex Marlowe" in result.text


def test_template_without_llm_is_valid_for_every_fixture(profile, load_posting):
    for job_id in (f"390000000{i}" for i in range(1, 9)):
        posting = load_posting(job_id)
        text = write_cover_note(profile, posting, keyword_rank(profile, posting)).text
        assert validate_text(text, profile, posting).ok, (job_id, text)


def test_template_drops_posting_data_when_it_would_break_never_claim(profile, load_posting):
    posting = load_posting("3900000001").model_copy(update={"title": "Blockchain Intern"})
    result = write_cover_note(profile, posting, keyword_rank(profile, posting))
    assert "Blockchain" not in result.text and validate_text(result.text, profile, posting).ok


def test_unknown_fact_ids_from_the_llm_are_dropped_and_logged(profile, load_posting, caplog):
    posting = load_posting("3900000001")
    baseline = keyword_rank(profile, posting)
    reply = json.dumps({"fact_order": ["proj-bandit", "made-up-fact", "proj-rag-search"],
                        "bullet_order": {"proj-rag-search": ["proj-rag-search-b2", "invented-bullet"]}})
    with caplog.at_level(logging.WARNING, logger="jac.tailor"):
        result = llm_rerank(FakeLLM([reply]), profile, posting, baseline)
    assert "made-up-fact" not in result.fact_order and "invented-bullet" not in result.priority
    assert result.dropped_ids == ["invented-bullet", "made-up-fact"]
    assert result.fact_order[:2] == ["proj-bandit", "proj-rag-search"]
    assert sorted(result.fact_order) == sorted(baseline.fact_order)  # nothing else was lost
    assert result.bullet_order["proj-rag-search"][0] == "proj-rag-search-b2"
    assert "made-up-fact" in caplog.text


def test_invalid_rerank_reply_keeps_the_baseline(profile, load_posting):
    posting = load_posting("3900000001")
    baseline = keyword_rank(profile, posting)
    assert llm_rerank(FakeLLM(["no json"]), profile, posting, baseline) is baseline


def test_keyword_rank_puts_the_matching_project_first(profile, load_posting):
    selection = keyword_rank(profile, load_posting("3900000001"))
    projects = [f for f in selection.fact_order if f.startswith("proj-")]
    assert projects[0] == "proj-rag-search"


class FakePageCounter:
    """Pretends the CV has one page per `budget` characters of visible text."""

    def __init__(self, budget: int) -> None:
        self.budget, self.calls = budget, 0

    def count(self, html: str) -> int:
        self.calls += 1
        return 1 + len(html_to_text(html)) // self.budget


def test_cv_that_fits_is_left_alone(profile, load_posting):
    posting = load_posting("3900000001")
    cv = render_cv(profile, keyword_rank(profile, posting), FakePageCounter(10**6))
    assert cv.fitted and cv.pages == 1
    assert "proj-bandit-b1" in cv.bullet_ids and "pub-preprint" in cv.fact_ids


def test_lowest_ranked_bullets_are_dropped_until_one_page(profile, load_posting):
    posting = load_posting("3900000001")
    selection = keyword_rank(profile, posting)
    full = render_cv(profile, selection, FakePageCounter(10**6))
    tight = render_cv(profile, selection, FakePageCounter(len(html_to_text(full.html)) - 150))
    assert tight.fitted and tight.pages == 1
    removed = set(full.bullet_ids) - set(tight.bullet_ids)
    assert removed
    protected = {ids[0] for ids in selection.bullet_order.values() if ids}
    assert not removed & protected  # every entry keeps its best bullet
    rank = {b: i for i, b in enumerate(selection.priority)}
    kept_droppable = [rank[b] for b in tight.bullet_ids if b not in protected]
    assert max(kept_droppable, default=-1) < min(rank[b] for b in removed)  # lowest-ranked go first


def test_projects_are_dropped_after_bullets_and_unfittable_cv_is_flagged(profile, load_posting):
    posting = load_posting("3900000001")
    cv = render_cv(profile, keyword_rank(profile, posting), FakePageCounter(400))
    assert not cv.fitted and cv.pages > 1
    assert len([f for f in cv.fact_ids if f.startswith("proj-")]) == 1


def test_rendered_cv_contains_only_bank_text(profile: Profile, load_posting):
    posting = load_posting("3900000001")
    cv = render_cv(profile, keyword_rank(profile, posting), HeuristicPageCounter())
    text = html_to_text(cv.html)
    bank = {b.id: re.sub(r"\*+", "", b.text) for f in profile.facts for b in f.bullets}
    for bullet_id in cv.bullet_ids:
        if "&" not in bank[bullet_id]:
            assert bank[bullet_id] in text
    assert profile.identity.name in text


def test_cv_html_escapes_profile_text(profile, load_posting):
    nasty = profile.model_copy(update={"summary": "<script>alert(1)</script> **bold**"})
    cv = render_cv(nasty, keyword_rank(nasty, load_posting("3900000001")), HeuristicPageCounter())
    assert "<script>alert" not in cv.html and "&lt;script&gt;" in cv.html and "<b>bold</b>" in cv.html


def test_build_application_writes_files_without_a_browser(profile, load_posting, tmp_path):
    posting = load_posting("3900000001")
    result = build_application(profile, posting, tmp_path / "job", llm=None, browser=None)
    assert result.cv_pdf is None and result.cv_html.exists() and result.cover_note.exists()
    assert (tmp_path / "job" / "meta.json").exists()
    assert any("No Chrome/Edge" in n for n in result.notes)
    assert "Dear hiring team at Northwind Data Lab" in result.cover_note.read_text(encoding="utf-8")
