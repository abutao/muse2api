"""Runtime settings loaded from environment / .env (no secrets committed)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
STATIC_DIR = ROOT_DIR / "static"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    host: str = "0.0.0.0"
    port: int = 8090
    public_base: str = "http://127.0.0.1:8090"
    tz: str = "Asia/Shanghai"

    database_url: str = "sqlite:///./data/muse-proxy.db"

    admin_user: str = "admin"
    admin_password: str = "change-me"
    session_secret: str = "change-me-session-secret"

    # Deprecated dual-run muse2api (must stay empty; P5 removed _fwd/19080)
    upstream_base_url: str = Field(
        default="",  # deprecated dual-run; prefer ENGINE_BACKEND=local
        validation_alias=AliasChoices(
            "upstream_base_url", "UPSTREAM_BASE_URL", "UPSTREAM_BASE"
        ),
    )
    upstream_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "upstream_api_key", "UPSTREAM_API_KEY", "MUSE2API_KEY"
        ),
    )

    # Outbound API keys for /v1 (M2: env list; M5 will add DB table)
    muse_proxy_key: str = Field(
        default="",
        validation_alias=AliasChoices("muse_proxy_key", "MUSE_PROXY_KEY"),
    )
    proxy_api_keys: str = Field(
        default="",
        validation_alias=AliasChoices("proxy_api_keys", "PROXY_API_KEYS"),
    )

    connect_timeout_sec: float = 10
    request_timeout_sec: float = 600
    stream_idle_timeout_sec: float = 120
    max_concurrency: int = 8

    media_auth_required: bool = True
    rewrite_media_urls: bool = True
    # Alias GENERATE_PLAYGROUND / generate_playground (settings API later)
    console_generate_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "console_generate_enabled",
            "CONSOLE_GENERATE_ENABLED",
            "generate_playground",
            "GENERATE_PLAYGROUND",
        ),
    )
    console_token_ttl_sec: int = Field(
        default=900,
        validation_alias=AliasChoices(
            "console_token_ttl_sec", "CONSOLE_TOKEN_TTL_SEC"
        ),
    )
    record_keep_days: int = 30
    charge_on_upstream_error: bool = False

    # Generation backend: local only (upstream dual-run removed)
    engine_backend: str = Field(
        default="local",
        validation_alias=AliasChoices(
            "engine_backend", "ENGINE_BACKEND", "MUSE_ENGINE_BACKEND"
        ),
    )
    chromium_path: str = Field(
        default="",
        validation_alias=AliasChoices(
            "chromium_path", "CHROMIUM_PATH", "MUSE_PROXY_CHROMIUM", "MUSE2API_CHROMIUM"
        ),
    )
    cdp_port: int = Field(
        default=19211,
        validation_alias=AliasChoices("cdp_port", "CDP_PORT", "MUSE_PROXY_CDP_PORT"),
    )
    engine_concurrency: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "engine_concurrency", "ENGINE_CONCURRENCY", "MUSE_ENGINE_CONCURRENCY"
        ),
    )

    signup_credentials_key: str = ""
    signup_webhook_token: str = ""
    # http (default, real muse.ai) | mock (offline QA only)
    signup_client: str = Field(
        default="http",
        validation_alias=AliasChoices("signup_client", "SIGNUP_CLIENT"),
    )
    signup_muse_base: str = Field(
        default="https://muse.ai",
        validation_alias=AliasChoices(
            "signup_muse_base", "SIGNUP_MUSE_BASE", "MUSE_SIGNUP_BASE"
        ),
    )
    # ---- checkout / card pool (age-verification $1 authorization) ----
    checkout_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "checkout_enabled", "CHECKOUT_ENABLED", "CARD_PAY_ENABLED"
        ),
    )
    checkout_amount: str = Field(
        default="1.00",
        validation_alias=AliasChoices("checkout_amount", "CHECKOUT_AMOUNT"),
    )
    checkout_currency: str = Field(
        default="USD",
        validation_alias=AliasChoices("checkout_currency", "CHECKOUT_CURRENCY"),
    )
    checkout_product_id: str = Field(
        default="766358168544755",
        validation_alias=AliasChoices("checkout_product_id", "CHECKOUT_PRODUCT_ID"),
    )
    checkout_receiver_id: str = Field(
        default="352501280694489",
        validation_alias=AliasChoices("checkout_receiver_id", "CHECKOUT_RECEIVER_ID"),
    )
    checkout_max_card_tries: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "checkout_max_card_tries", "CHECKOUT_MAX_CARD_TRIES"
        ),
    )
    checkout_ptt_mode: str = Field(
        default="jwe",
        validation_alias=AliasChoices(
            "checkout_ptt_mode", "CHECKOUT_PTT_MODE", "PTT_MODE"
        ),
    )

    proxy_scheduled_keepalive: bool = False
    # ---- registration automation ----
    # curl_cffi client-hello (JA3/JA4) to match a real browser; "" / off = httpx
    signup_tls_impersonate: str = Field(
        default="chrome",
        validation_alias=AliasChoices(
            "signup_tls_impersonate", "SIGNUP_TLS_IMPERSONATE", "TLS_IMPERSONATE"
        ),
    )
    # after registration: recovery-start → mail code → recovery-complete(random pwd)
    signup_auto_password: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "signup_auto_password", "SIGNUP_AUTO_PASSWORD", "AUTO_SET_PASSWORD"
        ),
    )
    # seconds to wait for an OTP / recovery mail before giving up (0 = single quick check)
    signup_mail_wait_sec: int = Field(
        default=180,
        validation_alias=AliasChoices(
            "signup_mail_wait_sec", "SIGNUP_MAIL_WAIT_SEC", "MAIL_WAIT_SEC"
        ),
    )
    signup_mail_poll_sec: float = Field(
        default=5.0,
        validation_alias=AliasChoices(
            "signup_mail_poll_sec", "SIGNUP_MAIL_POLL_SEC", "MAIL_POLL_SEC"
        ),
    )
    default_region: str = "US"
    default_into_pool: bool = True
    queue_wait_sec: int = 25
    signup_concurrency: int = 2
    signup_interval_sec: int = 30
    signup_daily_enabled: bool = False
    signup_daily_hour: int = 10
    signup_daily_minute: int = 0
    signup_daily_count: int = 20
    acct_min_interval_sec: int = 20
    acct_interval_jitter_pct: int = 40
    acct_global_daily_cap: int = 0
    acct_daily_quota: int = 3
    acct_rotate_attempts: int = 5
    acct_408_cooldown_min: int = 3
    acct_408_disable_streak: int = 3
    acct_cooldown_cap_min: int = 30
    acct_403_stop_hours: int = 24
    proxies: str = ""
    result_mode: str = "direct"  # direct | r2
    token_prefix: str = "mprox_"

    @field_validator("upstream_base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return (v or "").rstrip("/")

    def outbound_keys(self) -> set[str]:
        keys: set[str] = set()
        if self.muse_proxy_key.strip():
            keys.add(self.muse_proxy_key.strip())
        for part in self.proxy_api_keys.split(","):
            p = part.strip()
            if p:
                keys.add(p)
        return keys


@lru_cache
def _base_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()


def get_settings() -> Settings:
    """Env defaults + SQLite app_settings overrides (M8)."""
    base = _base_settings()
    try:
        from app.runtime_settings import load_overrides

        ov = load_overrides()
    except Exception:
        return base
    if not ov:
        return base
    data = base.model_dump()
    for k, v in ov.items():
        if k in data:
            data[k] = v
    return Settings(**data)


def reload_settings() -> Settings:
    _base_settings.cache_clear()
    return get_settings()
