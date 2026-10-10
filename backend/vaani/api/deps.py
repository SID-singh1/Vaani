from __future__ import annotations

from fastapi import Request

from ..context import AppContext
from ..errors import Unauthorized
from ..security import secret_matches, verify_token


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def current_user(request: Request) -> str:
    """User id from a signed `Authorization: Bearer <token>` header."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized(detail="missing bearer token")
    user_id = verify_token(get_ctx(request).settings.secret_key, token.strip())
    if user_id is None:
        raise Unauthorized(detail="invalid token")
    return user_id


def require_admin(request: Request) -> None:
    expected = get_ctx(request).settings.admin_secret_key
    if not expected:
        raise Unauthorized("The admin dashboard is disabled: set ADMIN_SECRET_KEY on the server.")
    if not secret_matches(request.headers.get("x-admin-key"), expected):
        raise Unauthorized("Invalid admin key.")
