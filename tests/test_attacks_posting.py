"""Review findings on posting fetch and posting text. Each case misbehaved before its fix."""
import urllib.request
from email.message import Message

import pytest

from jac.config import FetchConfig
from jac.fit.rules import Outcome, rule_required_languages
from jac.postings import PostingFetcher, html_to_text
from test_postings import FakeResponse, good, make_fetcher


def test_redirect_follows_the_location_that_was_given(tmp_path):
    target = "https://www.linkedin.com/jobs/view/3900000001/?trk=guest"
    fetcher, session, _ = make_fetcher(tmp_path, [FakeResponse(301, headers={"Location": target}), good()])
    assert fetcher.fetch("3900000001").status == "ok"
    assert session.calls[1][0] == target


@pytest.mark.parametrize("location", [
    "https://www.linkedin.com/jobs/view/3999999999",
    "//evil.tld/jobs/view/3900000001",
    "https://www.linkedin.com@evil.tld/jobs/view/3900000001",
    "http://www.linkedin.com/jobs/view/3900000001",
])
def test_redirect_anywhere_else_halts(tmp_path, location):
    fetcher, session, _ = make_fetcher(tmp_path, [FakeResponse(302, headers={"Location": location})])
    assert fetcher.fetch("3900000001").status == "blocked"
    assert len(session.calls) == 1


def test_default_session_never_stores_cookies(tmp_path):
    jar = PostingFetcher(FetchConfig(), tmp_path)._session.cookies

    class Reply:
        def info(self) -> Message:
            msg = Message()
            msg["Set-Cookie"] = "bcookie=abc; Domain=.linkedin.com; Path=/; Secure"
            return msg

    jar.extract_cookies(Reply(), urllib.request.Request("https://www.linkedin.com/jobs/view/1"))
    assert len(jar) == 0


def test_white_background_is_not_hidden_text():
    assert "Fluent Dutch is required." in html_to_text('<p style="background-color:#ffffff">Fluent Dutch is required.</p>')


@pytest.mark.parametrize("style", [
    "position:absolute; left:-20000px",
    "clip: rect(0 0 0 0); position:absolute",
    "transform: scale(0)",
    "width:0;height:0;overflow:hidden",
    "overflow:hidden; height:0",
    "max-height:0;overflow:hidden",
    "color: #FFF",
])
def test_more_ways_of_hiding_text_are_dropped(style):
    assert html_to_text(f'<p>Visible</p><p style="{style}">fully remote, 8 hours per week</p>') == "Visible"


def test_soft_word_in_another_clause_does_not_soften_a_requirement(profile, load_posting):
    posting = load_posting("3900000001").model_copy(update={"description": "Fluent Dutch is required; English is a plus."})
    assert rule_required_languages(profile, posting).outcome is Outcome.REJECT
