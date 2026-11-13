"""Review findings on mail authenticity and links. Each case was accepted before its fix."""
import email
import email.policy

import pytest

from jac.sources import LINKEDIN, canonical_job_url
from jac.verify import _parse_auth_results, verify_message
from mailfactory import GOOD_AUTH, build_mail

TRUSTED = ["mx.google.com"]
SENDER = "jobalerts-noreply@linkedin.com"
SMUGGLED = "x); dkim=pass header.i=@linkedin.com; dmarc=pass header.from=linkedin.com; (y"


def check(raw: bytes):
    return verify_message(email.message_from_bytes(raw, policy=email.policy.default), LINKEDIN, TRUSTED)


def mail_with_headers(*lines: str) -> bytes:
    """A mail whose header block is exactly these lines plus a body with one job link."""
    body = "Content-Type: text/plain\r\n\r\nhttps://www.linkedin.com/jobs/view/3901234567\r\n"
    return ("\r\n".join(lines) + "\r\n" + body).encode()


def test_quoted_mailfrom_cannot_smuggle_results_out_of_a_comment():
    """The receiver echoes the envelope sender inside a comment; a ')' in a quoted local part
    used to end the comment early and inject dkim=pass and dmarc=pass segments."""
    top = (f'mx.google.com; dkim=fail header.i=@evil.tld; '
           f'spf=pass (google.com: domain of "{SMUGGLED}"@evil.tld designates 1.2.3.4 as permitted sender) '
           f'smtp.mailfrom="{SMUGGLED}"@evil.tld; dmarc=fail (p=REJECT) header.from=linkedin.com')
    _, results = _parse_auth_results(top)
    assert [r for r, _ in results["dkim"]] == ["fail"] and [r for r, _ in results["dmarc"]] == ["fail"]
    verdict = check(build_mail(auth=[top]))
    assert not verdict.accepted and verdict.job_urls == []


def test_nested_comments_are_skipped():
    _, results = _parse_auth_results("mx.google.com; dkim=fail (a (b) dkim=pass header.d=linkedin.com) header.d=evil.tld")
    assert results["dkim"] == [("fail", {"header.d": "evil.tld"})]


def test_any_failing_dmarc_result_in_the_trusted_header_quarantines():
    assert not check(build_mail(auth=[GOOD_AUTH + "; dmarc=fail header.from=linkedin.com"])).accepted


def test_dmarc_pass_must_name_the_sender_domain():
    verdict = check(build_mail(auth=[GOOD_AUTH.replace(" header.from=linkedin.com", "")]))
    assert not verdict.accepted
    assert any("header.from" in r for r in verdict.reasons)


def test_encoded_word_that_decodes_to_an_allowed_address_is_quarantined():
    raw = mail_with_headers(f"Authentication-Results: {GOOD_AUTH}", "From: =?utf-8?q?jobalerts-noreply=40linkedin.com?=")
    assert not check(raw).accepted


def test_header_line_hidden_from_the_parser_is_quarantined():
    """'From :' is obsolete syntax that some receivers read and Python skips. Disagreement means quarantine."""
    raw = mail_with_headers(f"Authentication-Results: {GOOD_AUTH}", f"From: {SENDER}", "From : evil@evil.tld")
    verdict = check(raw)
    assert not verdict.accepted
    assert any("malformed" in r for r in verdict.reasons)


def test_second_address_in_a_from_comment_is_quarantined():
    raw = mail_with_headers(f"Authentication-Results: {GOOD_AUTH}", f"From: {SENDER} (evil@evil.tld)")
    assert not check(raw).accepted


def test_sender_header_elsewhere_is_quarantined():
    verdict = check(build_mail(extra_headers=[("Sender", "bulk@evil.tld")]))
    assert not verdict.accepted
    assert any("Sender" in r for r in verdict.reasons)


def test_folded_and_lower_case_headers_still_verify():
    raw = mail_with_headers(
        "authentication-results: mx.google.com;\r\n dkim=pass header.i=@linkedin.com;\r\n"
        "\tspf=pass smtp.mailfrom=jobalerts-noreply@linkedin.com;\r\n dmarc=pass header.from=linkedin.com",
        f"from: LinkedIn\r\n <{SENDER}>",
    )
    verdict = check(raw)
    assert verdict.accepted, verdict.reasons


def test_relative_links_under_a_base_tag_are_not_resolved():
    msg = email.message_from_bytes(build_mail(links=[]), policy=email.policy.default)
    msg.get_body(("html",)).set_content(
        '<html><head><base href="https://evil.tld/"></head><body><a href="/jobs/view/3901234567">job</a>'
        '<img src="https://evil.tld/jobs/view/1"></body></html>', subtype="html")
    verdict = verify_message(msg, LINKEDIN, TRUSTED)
    assert verdict.accepted and verdict.job_urls == []


@pytest.mark.parametrize("digits", ["３９０１２３４５６７", "٣٩٠١٢٣٤٥٦٧"])
def test_non_ascii_digits_are_not_a_job_id(digits):
    assert canonical_job_url(f"https://www.linkedin.com/jobs/view/{digits}") is None
