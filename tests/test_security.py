import pytest
from starlette.requests import Request

from vaani.api.ratelimit import RateLimited, RateLimiter, client_ip, parse_limit
from vaani.security import (
    derive_secret,
    issue_token,
    new_web_user_id,
    pseudonymize,
    secret_matches,
    verify_token,
)


def test_token_roundtrip():
    user = new_web_user_id()
    assert verify_token("k", issue_token("k", user)) == user


def test_token_rejects_tampering_and_wrong_key():
    token = issue_token("k", "web_abcdefghij")
    version, user, sig = token.split(".")
    assert verify_token("other-key", token) is None
    assert verify_token("k", f"{version}.web_zzzzzzzzzz.{sig}") is None
    assert verify_token("k", "garbage") is None
    assert verify_token("k", "") is None


def test_tokens_only_for_web_ids():
    with pytest.raises(ValueError):
        issue_token("k", "tg_12345")
    assert verify_token("k", issue_token("k", "user_ab12cd")) == "user_ab12cd"  # legacy web id


def test_secret_matches_never_accepts_unset():
    assert not secret_matches("", "")
    assert not secret_matches(None, "x")
    assert not secret_matches("x", "")
    assert secret_matches("abc", "abc")


def test_derived_and_pseudonymous_ids_are_stable():
    assert derive_secret("k", "a") == derive_secret("k", "a") != derive_secret("k", "b")
    phone = "919876543210"
    pid = pseudonymize("k", phone, "wa_")
    assert pid == pseudonymize("k", phone, "wa_") and pid.startswith("wa_") and phone not in pid


def _request(headers: dict, host: str = "10.0.0.1") -> Request:
    return Request(
        {"type": "http", "headers": [(k.encode(), v.encode()) for k, v in headers.items()], "client": (host, 1234)}
    )


def test_client_ip_uses_rightmost_trusted_hop():
    req = _request({"x-forwarded-for": "6.6.6.6, 203.0.113.9"})
    assert client_ip(req, 1) == "203.0.113.9"  # 6.6.6.6 is client-supplied and spoofable
    assert client_ip(req, 0) == "10.0.0.1"


def test_rate_limiter():
    limiter = RateLimiter(*parse_limit("2/minute"))
    limiter.hit("a")
    limiter.hit("a")
    with pytest.raises(RateLimited):
        limiter.hit("a")
    limiter.hit("b")  # independent key


def test_parse_limit_rejects_garbage():
    with pytest.raises(ValueError):
        parse_limit("lots")
