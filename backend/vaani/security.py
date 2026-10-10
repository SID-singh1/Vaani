"""Signed anonymous session tokens and secret helpers.

Web users don't create accounts. On first visit the server issues a random user id plus an
HMAC signature over it; every API call presents that token, so a user can only ever act on
their own notes. Telegram and WhatsApp users are identified by their platform and never get
web tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

TOKEN_VERSION = "v1"  # noqa: S105 - format version, not a secret
WEB_USER_PREFIX = "web_"
# Ids created by the pre-v2 web client: 'user_' + 9 base36 chars from Math.random().
LEGACY_WEB_ID = re.compile(r"^user_[a-z0-9]{4,12}$")
_VALID_WEB_ID = re.compile(r"^(web_[A-Za-z0-9_-]{8,40}|user_[a-z0-9]{4,12})$")


def _sign(secret_key: str, message: str) -> str:
    digest = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def new_web_user_id() -> str:
    return WEB_USER_PREFIX + secrets.token_urlsafe(12)


def issue_token(secret_key: str, user_id: str) -> str:
    if not _VALID_WEB_ID.match(user_id):
        raise ValueError("tokens are only issued for web user ids")
    payload = f"{TOKEN_VERSION}.{user_id}"
    return f"{payload}.{_sign(secret_key, payload)}"


def verify_token(secret_key: str, token: str) -> str | None:
    """Return the user id if the token is authentic, else None."""
    try:
        version, user_id, signature = token.split(".")
    except ValueError:
        return None
    if version != TOKEN_VERSION or not _VALID_WEB_ID.match(user_id):
        return None
    expected = _sign(secret_key, f"{version}.{user_id}")
    if not hmac.compare_digest(signature, expected):
        return None
    return user_id


def secret_matches(provided: str | None, expected: str | None) -> bool:
    """Constant-time comparison that never accepts an unset secret."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


def derive_secret(secret_key: str, purpose: str) -> str:
    """Deterministic per-purpose secret, so fewer secrets need configuring by hand."""
    return _sign(secret_key, f"derive:{purpose}")[:48]


def pseudonymize(secret_key: str, value: str, prefix: str) -> str:
    """Stable, non-reversible id for identifiers that are personal data (e.g. phone numbers)."""
    return prefix + _sign(secret_key, f"pseudonym:{value}")[:22]
