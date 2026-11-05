"""muse-proxy FastAPI application entry."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.config import STATIC_DIR, get_settings
from app.db import init_db
from app.routers import accounts, admin_auth, cards, generate, health, overview, r2, records, refills, subs, v1
from app.routers import settings as settings_router

settings = get_settings()
init_db()

# clear persisted dual-run UPSTREAM overrides (P5)
try:
    from app.runtime_settings import set_overrides
    set_overrides({"upstream_base_url": "", "upstream_api_key": ""})
except Exception:
    pass

app = FastAPI(
    title="muse-proxy",
    version=__version__,
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="muse_proxy_session",
    same_site="lax",
    https_only=False,
    max_age=60 * 60 * 24 * 7,
)

app.include_router(health.router)
app.include_router(admin_auth.router)
app.include_router(v1.router)

app.include_router(generate.router, prefix="/api")
app.include_router(records.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(accounts.router, prefix="/api")

app.include_router(subs.router, prefix="/api")
app.include_router(cards.router, prefix="/api")
app.include_router(overview.router, prefix="/api")
app.include_router(refills.router, prefix="/api")
app.include_router(r2.router, prefix="/api")

@app.on_event("startup")
async def _startup_keepalive() -> None:
    from app import jobs_keepalive
    from app.signup import scheduler as signup_scheduler

    jobs_keepalive.ensure_scheduler()
    signup_scheduler.ensure_scheduler()



@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    # /v1 already uses OpenAI-shaped detail dicts from auth_api/proxy.
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    if request.url.path.startswith("/v1/"):
        message = detail if isinstance(detail, str) else str(detail)
        status = exc.status_code
        err_type = (
            "rate_limit_error"
            if status == 429
            else "not_found_error"
            if status == 404
            else "server_error"
            if status >= 500
            else "invalid_request_error"
        )
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "message": message,
                    "type": err_type,
                    "param": None,
                    "code": status,
                }
            },
        )
    if isinstance(detail, dict):
        body = detail
    else:
        body = {"error": detail if detail else "error"}
    return JSONResponse(status_code=exc.status_code, content=body)


@app.get("/")
def index_page(request: Request):
    if not request.session.get("admin_user"):
        return RedirectResponse(url="/login.html", status_code=302)
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login.html")
def login_page():
    return FileResponse(STATIC_DIR / "login.html")


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=False), name="static")


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
