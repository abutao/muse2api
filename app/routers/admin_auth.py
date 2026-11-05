"""Admin login / me / logout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth_admin import (
    login_session,
    logout_session,
    me_payload,
    require_admin,
    verify_admin_password,
)
from app.config import get_settings

router = APIRouter(prefix="/api", tags=["admin-auth"])


class LoginBody(BaseModel):
    user: str = Field(min_length=1)
    password: str = Field(min_length=1)


@router.post("/login")
def api_login(body: LoginBody, request: Request):
    settings = get_settings()
    if not verify_admin_password(body.user.strip(), body.password, settings):
        return JSONResponse(
            status_code=401,
            content={"error": "用户名或密码错误"},
        )
    login_session(request, body.user.strip())
    return {"ok": True, "user": body.user.strip()}


@router.get("/me")
def api_me(user: str = Depends(require_admin)) -> dict:
    return me_payload(user)


@router.post("/logout")
def api_logout(request: Request) -> dict:
    logout_session(request)
    return {"ok": True}
