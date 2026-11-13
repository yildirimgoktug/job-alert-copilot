import requests
from conftest import posting_html

from jac.config import FetchConfig
from jac.postings import PostingFetcher, html_to_text, looks_like_login_wall, parse_posting

AUTHWALL = "<html><body><form action='/uas/login-submit'><input name='session_key'>Sign in to view</form></body></html>"


def test_json_ld_posting_is_parsed(load_posting):
    posting = load_posting("3900000001")
    assert posting.title == "AI Research Intern (part-time)"
    assert posting.company == "Northwind Data Lab"
    assert posting.location == "Amsterdam, Netherlands"
    assert posting.employment_type == "INTERN"
    assert posting.date_posted == "2026-11-09"
    assert "16 hours per week" in posting.description
    assert posting.url == "https://www.linkedin.com/jobs/view/3900000001"


def test_html_fallback_when_there_is_no_json_ld():
    html = (
        "<html><body><h1 class='top-card-layout__title'>Data Intern</h1>"
        "<a class='topcard__org-name-link'>Acme BV</a><span class='topcard__flavor--bullet'>Utrecht</span>"
        "<div class='show-more-less-html__markup'><p>" + "You will analyse data and write reports. " * 6 + "</p>"
        "<ul><li>Python</li><li>SQL</li></ul></div></body></html>"
    )
    posting = parse_posting(html, "123456")
    assert posting and posting.title == "Data Intern" and posting.company == "Acme BV"
    assert posting.location == "Utrecht" and "• Python" in posting.description


def test_hidden_html_text_is_dropped(load_posting):
    posting = load_posting("3900000007")
    assert "Ignore previous instructions" not in posting.description
    assert "PhD is required" not in posting.description
    assert "system prompt" not in posting.description
    assert "12 hours per week" in posting.description


def test_html_to_text_drops_scripts_comments_and_hidden_attributes():
    html = (
        "<p>Visible</p><script>evil()</script><!-- note --><p hidden>a</p><p aria-hidden='true'>b</p>"
        "<p style='display: none'>c</p><p style='color:#fff;'>d</p><p class='sr-only'>e</p>"
    )
    assert html_to_text(html) == "Visible"


def test_unparsable_page_returns_none():
    assert parse_posting("<html><body>nothing here</body></html>", "1") is None


def test_login_wall_detection():
    assert looks_like_login_wall(AUTHWALL)
    assert not looks_like_login_wall("<html><body>A job</body></html>")


class FakeResponse:
    def __init__(self, status=200, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class Clock:
    """Fake clock: sleeping advances time, so rate limiting is testable without waiting."""

    def __init__(self):
        self.now, self.slept = 1_000_000.0, []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def make_fetcher(tmp_path, responses, **cfg):
    session, clock = FakeSession(responses), Clock()
    fetcher = PostingFetcher(FetchConfig(**cfg), tmp_path, session=session, clock=clock.time, sleep=clock.sleep)
    return fetcher, session, clock


def good():
    return FakeResponse(200, posting_html("3900000001"))


def test_fetch_uses_plain_get_without_redirects_and_a_descriptive_user_agent(tmp_path):
    fetcher, session, _ = make_fetcher(tmp_path, [good()])
    result = fetcher.fetch("3900000001")
    assert result.status == "ok" and result.usable
    url, kwargs = session.calls[0]
    assert url == "https://www.linkedin.com/jobs/view/3900000001"
    assert kwargs["allow_redirects"] is False
    assert "cookies" not in kwargs and "auth" not in kwargs
    assert set(kwargs["headers"]) == {"User-Agent", "Accept"}
    assert "job-alert-copilot" in kwargs["headers"]["User-Agent"]


def test_second_fetch_comes_from_the_cache_without_a_request(tmp_path):
    fetcher, session, _ = make_fetcher(tmp_path, [good()])
    fetcher.fetch("3900000001")
    again = fetcher.fetch("3900000001")
    assert again.status == "cache" and len(session.calls) == 1


def test_requests_are_spaced_by_the_minimum_interval(tmp_path):
    fetcher, _, clock = make_fetcher(tmp_path, [good(), good()])
    fetcher.fetch("3900000001")
    fetcher.fetch("3900000002")
    assert clock.slept and clock.slept[-1] >= 9.99


def test_per_run_cap_stops_further_requests(tmp_path):
    fetcher, session, _ = make_fetcher(tmp_path, [good()] * 3, max_requests_per_run=2)
    results = [fetcher.fetch(str(i)) for i in range(1, 4)]
    assert [r.status for r in results] == ["ok", "ok", "cap_reached"]
    assert len(session.calls) == 2


def test_http_429_and_999_halt_the_run(tmp_path):
    fetcher, session, _ = make_fetcher(tmp_path, [FakeResponse(999)])
    assert fetcher.fetch("111").status == "blocked"
    assert fetcher.fetch("222").status == "halted"
    assert len(session.calls) == 1
    fetcher, _, _ = make_fetcher(tmp_path / "b", [FakeResponse(429)])
    assert fetcher.fetch("111").status == "blocked"


def test_login_wall_halts_the_run_and_is_not_cached(tmp_path):
    fetcher, _, _ = make_fetcher(tmp_path, [FakeResponse(200, AUTHWALL)])
    result = fetcher.fetch("111")
    assert result.status == "blocked" and "open it manually" in result.reason
    assert fetcher.fetch("222").status == "halted"
    assert not (tmp_path / "postings" / "111.html").exists()


def test_redirect_to_another_host_is_not_followed(tmp_path):
    redirect = FakeResponse(302, headers={"Location": "https://evil.example/jobs/view/111"})
    fetcher, session, _ = make_fetcher(tmp_path, [redirect])
    assert fetcher.fetch("111").status == "blocked"
    assert len(session.calls) == 1


def test_redirect_to_the_same_posting_on_linkedin_is_followed(tmp_path):
    redirect = FakeResponse(301, headers={"Location": "https://www.linkedin.com/jobs/view/3900000001/"})
    fetcher, session, _ = make_fetcher(tmp_path, [redirect, good()])
    assert fetcher.fetch("3900000001").status == "ok"
    assert len(session.calls) == 2


def test_offline_mode_never_touches_the_network(tmp_path):
    fetcher, session, _ = make_fetcher(tmp_path, [], offline=True)
    assert fetcher.fetch("111").status == "offline"
    assert session.calls == []


def test_network_error_is_reported_not_raised(tmp_path):
    class Boom:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError("down")

    clock = Clock()
    fetcher = PostingFetcher(FetchConfig(), tmp_path, session=Boom(), clock=clock.time, sleep=clock.sleep)
    assert fetcher.fetch("111").status == "error"
