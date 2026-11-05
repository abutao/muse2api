"""Admin cookie-session helpers (browser console)."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings

SESSION_USER_KEY = "admin_user"


def verify_admin_password(user: str, password: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    from app.runtime_settings import admin_password as runtime_admin_password

    return user == settings.admin_user and password == runtime_admin_password()


def get_session_user(request: Request) -> str | None:
    return request.session.get(SESSION_USER_KEY)


def require_admin(request: Request) -> str:
    user = get_session_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized"},
        )
    return user


def login_session(request: Request, user: str) -> None:
    request.session.clear()
    request.session[SESSION_USER_KEY] = user


def logout_session(request: Request) -> None:
    request.session.clear()


def me_payload(user: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {
        "user": user,
        "role": "admin",
        "public_base": settings.public_base,
        "upstream_configured": False,  # P5: UPSTREAM/19080 removed
        "engine_backend": "local",
    }
