import email
import email.policy

import pytest

from jac.config import ConfigError
from jac.sources import LINKEDIN, canonical_job_url
from jac.verify import verify_message
from mailfactory import GOOD_AUTH, build_mail, job_link

TRUSTED = ["mx.google.com"]


def check(raw: bytes):
    return verify_message(email.message_from_bytes(raw, policy=email.policy.default), LINKEDIN, TRUSTED)


def test_authentic_alert_is_accepted_and_urls_are_canonical():
    verdict = check(build_mail([job_link("3901234567"), job_link("3907654321")]))
    assert verdict.accepted, verdict.reasons
    assert verdict.job_urls == [
        "https://www.linkedin.com/jobs/view/3901234567",
        "https://www.linkedin.com/jobs/view/3907654321",
    ]


def test_tracking_parameters_are_stripped_and_duplicates_collapsed():
    verdict = check(build_mail([job_link("3901234567"), job_link("3901234567", "?other=1#frag")]))
    assert verdict.job_urls == ["https://www.linkedin.com/jobs/view/3901234567"]
    assert all("?" not in u and "trackingId" not in u for u in verdict.job_urls)


def test_spoofed_display_name_is_quarantined():
    raw = build_mail(sender="jobs-noreply@evil.example", display="jobs-noreply@linkedin.com")
    verdict = check(raw)
    assert not verdict.accepted
    assert verdict.job_urls == []
    assert any("From domain" in r for r in verdict.reasons)


def test_address_inside_display_name_does_not_fool_the_parser():
    raw = build_mail(sender="evil@evil.example", display="LinkedIn <jobs-noreply@linkedin.com>")
    assert not check(raw).accepted


@pytest.mark.parametrize(
    "sender",
    [
        "jobs-noreply@linkedln.com",
        "jobs-noreply@linkedin.com.evil.tld",
        "jobs-noreply@xn--linkedn-9ua.com",
        "jobs-noreply@mail.linkedin.com",
        "jobs-noreply@linkedin.com.",
    ],
)
def test_lookalike_sender_domains_are_quarantined(sender):
    assert not check(build_mail(sender=sender)).accepted


def test_unlisted_linkedin_address_is_quarantined():
    verdict = check(build_mail(sender="someone@linkedin.com"))
    assert not verdict.accepted
    assert any("allowed sender list" in r for r in verdict.reasons)


def test_forged_lower_authentication_results_is_ignored():
    failing = GOOD_AUTH.replace("dkim=pass", "dkim=fail").replace("dmarc=pass", "dmarc=fail")
    verdict = check(build_mail(auth=[failing, GOOD_AUTH]))
    assert not verdict.accepted
    assert any("dkim" in r for r in verdict.reasons)


def test_untrusted_top_header_with_forged_trusted_header_below_is_refused():
    verdict = check(build_mail(auth=["evil.example; dkim=pass header.d=linkedin.com; spf=pass; dmarc=pass", GOOD_AUTH]))
    assert not verdict.accepted
    assert any("trusted receiver" in r for r in verdict.reasons)


def test_dkim_fail_is_quarantined():
    assert not check(build_mail(auth=[GOOD_AUTH.replace("dkim=pass", "dkim=fail")])).accepted


def test_dkim_pass_for_another_domain_is_quarantined():
    other = GOOD_AUTH.replace("header.i=@linkedin.com", "header.i=@evil.example")
    verdict = check(build_mail(auth=[other]))
    assert not verdict.accepted
    assert any("dkim" in r for r in verdict.reasons)


def test_dkim_pass_with_header_d_is_enough():
    ok = GOOD_AUTH.replace("header.i=@linkedin.com", "header.d=linkedin.com")
    assert check(build_mail(auth=[ok])).accepted


def test_spf_softfail_is_quarantined():
    assert not check(build_mail(auth=[GOOD_AUTH.replace("spf=pass", "spf=softfail")])).accepted


def test_dmarc_missing_is_quarantined():
    without = GOOD_AUTH.split("; dmarc")[0]
    verdict = check(build_mail(auth=[without]))
    assert not verdict.accepted
    assert any("dmarc" in r for r in verdict.reasons)


def test_missing_authentication_results_is_quarantined():
    assert not check(build_mail(auth=[])).accepted


def test_reply_to_elsewhere_is_quarantined():
    verdict = check(build_mail(reply_to="hr@evil.example"))
    assert not verdict.accepted
    assert any("Reply-To" in r for r in verdict.reasons)


def test_reply_to_at_linkedin_is_fine():
    assert check(build_mail(reply_to="jobs-noreply@linkedin.com")).accepted


def test_two_from_headers_are_quarantined():
    raw = b"From: evil@evil.example\r\n" + build_mail()
    assert not check(raw).accepted


def test_quarantined_mail_is_never_parsed_for_links():
    verdict = check(build_mail(links=[job_link("3901234567")], auth=[GOOD_AUTH.replace("spf=pass", "spf=fail")]))
    assert verdict.job_urls == [] and verdict.rejected_links == []


def test_unsafe_links_in_an_accepted_mail_are_dropped():
    links = [
        "https://lnkd.in/abc123",
        "https://www.linkedin.com@evil.tld/jobs/view/3901234567",
        "https://www.linkedin.com.evil.tld/jobs/view/3901234567",
        "https://xn--linkedn-9ua.com/jobs/view/3901234567",
        "http://www.linkedin.com/jobs/view/3901234567",
        "https://www.linkedin.com/jobs/view/3901234567/../../in/someone",
        "https://www.linkedin.com/comm/profile/someone",
        "https://www.linkedin.com/jobs/view/3999999999",
    ]
    verdict = check(build_mail(links=links))
    assert verdict.accepted
    assert verdict.job_urls == ["https://www.linkedin.com/jobs/view/3999999999"]
    assert len(verdict.rejected_links) >= 7


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://linkedin.com/jobs/view/12345678", "https://www.linkedin.com/jobs/view/12345678"),
        ("https://www.linkedin.com/comm/jobs/view/12345678/?a=b", "https://www.linkedin.com/jobs/view/12345678"),
        ("https://WWW.LINKEDIN.COM/jobs/view/12345678", "https://www.linkedin.com/jobs/view/12345678"),
        ("https://www.linkedin.com:8443/jobs/view/12345678", None),
        ("https://www.linkedin.com/jobs/view/senior-engineer-12345678", None),
        ("https://www.linkedin.com/jobs/view/12345678\n@evil", None),
        ("javascript:alert(1)", None),
        ("https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Fevil.tld", None),
    ],
)
def test_canonical_job_url(url, expected):
    assert canonical_job_url(url) == expected


def test_verification_refuses_without_trusted_authserv_id():
    msg = email.message_from_bytes(build_mail(), policy=email.policy.default)
    with pytest.raises(ConfigError):
        verify_message(msg, LINKEDIN, [])


def test_wildcard_authserv_preset_matches_provider_subdomains():
    raw = build_mail(auth=[GOOD_AUTH.replace("mx.google.com", "mta123.mail.yahoo.com")])
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert verify_message(msg, LINKEDIN, ["*.mail.yahoo.com"]).accepted
    assert not verify_message(msg, LINKEDIN, ["*.mail.yahoo.com.evil.tld"]).accepted
