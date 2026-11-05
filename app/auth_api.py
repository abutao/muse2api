"""Outbound Bearer auth for /v1 (env keys + DB api_keys + ephemeral console)."""

from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException, Request

from app import api_keys, console_tokens
from app.config import get_settings


def extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def openai_http_error(
    status: int,
    message: str,
    *,
    code: str | int | None = None,
    err_type: str | None = None,
) -> HTTPException:
    if err_type is None:
        if status == 401:
            err_type = "invalid_request_error"
        elif status == 403:
            err_type = "invalid_request_error"
        elif status == 429:
            err_type = "rate_limit_error"
        elif status == 404:
            err_type = "not_found_error"
        elif status >= 500:
            err_type = "server_error"
        else:
            err_type = "invalid_request_error"
    return HTTPException(
        status_code=status,
        detail={
            "error": {
                "message": message,
                "type": err_type,
                "param": None,
                "code": code if code is not None else status,
            }
        },
    )


def _accept_bearer(authorization: str | None) -> tuple[str, str, dict[str, Any] | None]:
    """Return (token, kind, db_row_or_none). kind: console|env|user."""
    settings = get_settings()
    token = extract_bearer(authorization)
    if not token:
        raise openai_http_error(
            401,
            "Missing Authorization: Bearer <key>",
            code="missing_api_key",
        )

    console = console_tokens.validate(token)
    if console:
        if not settings.console_generate_enabled:
            raise openai_http_error(
                403,
                "Console playground disabled",
                code="console_disabled",
            )
        return token, "console", None

    if token in settings.outbound_keys():
        return token, "env", None

    row = api_keys.lookup_by_token(token)
    if row:
        api_keys.assert_key_usable(row, model=None)
        return token, "user", row

    env_keys = settings.outbound_keys()
    # helpful misconfig only when nothing configured at all
    with_db = False
    try:
        from app.db import get_db

        with get_db() as conn:
            with_db = (
                conn.execute(
                    "SELECT 1 FROM api_keys WHERE kind='user' AND enabled=1 LIMIT 1"
                ).fetchone()
                is not None
            )
    except Exception:
        with_db = False

    if not env_keys and not with_db:
        raise openai_http_error(
            503,
            "proxy has no outbound API keys configured "
            "(set MUSE_PROXY_KEY / PROXY_API_KEYS or create a key in settings)",
            code="proxy_misconfigured",
        )
    raise openai_http_error(401, "Invalid API key", code="invalid_api_key")


def _apply_auth_state(
    request: Request, token: str, kind: str, row: dict[str, Any] | None
) -> str:
    request.state.api_key = token
    request.state.key_kind = kind
    request.state.key_id = int(row["id"]) if row else None
    request.state.key_row = row
    # Charge only DB user keys (not console / env)
    request.state.charge_key = bool(row) and kind == "user"
    return token


def require_bearer_key(
    request: Request,
    authorization: str | None = Header(default=None),
) -> str:
    token, kind, row = _accept_bearer(authorization)
    return _apply_auth_state(request, token, kind, row)


def validate_bearer_key(authorization: str | None) -> str:
    token, _kind, _row = _accept_bearer(authorization)
    return token


def validate_bearer_key_detailed(
    authorization: str | None,
) -> tuple[str, str, dict[str, Any] | None]:
    return _accept_bearer(authorization)


def optional_bearer_from_request(request: Request) -> str | None:
    return extract_bearer(request.headers.get("authorization"))


def source_for_request(request: Request) -> str:
    kind = getattr(request.state, "key_kind", None)
    if kind == "console":
        return "admin_console"
    return "external"


def maybe_check_model(request: Request, model: str | None) -> None:
    row = getattr(request.state, "key_row", None)
    if row and model:
        api_keys.check_model_allowed(row, model)
