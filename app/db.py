"""SQLite helpers for muse-proxy."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from app.config import get_settings

def _settings_for_path():
    """Avoid circular import when get_settings merges app_settings."""
    try:
        from app.config import _base_settings
        return _base_settings()
    except Exception:
        return get_settings()

# No card columns anywhere.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    sid TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS call_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    path TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'POST',
    api_type TEXT,
    model TEXT,
    stream INTEGER NOT NULL DEFAULT 0,
    status INTEGER,
    upstream_status INTEGER,
    latency_ms INTEGER,
    source TEXT NOT NULL DEFAULT 'external',
    key_prefix TEXT,
    key_id INTEGER,
    error TEXT,
    prompt_preview TEXT,
    upstream_task_id TEXT,
    media_urls TEXT,
    request_id TEXT
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    token_hash TEXT NOT NULL UNIQUE,
    token_prefix TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'user',
    enabled INTEGER NOT NULL DEFAULT 1,
    quota_total INTEGER,
    quota_used INTEGER NOT NULL DEFAULT 0,
    daily_limit INTEGER,
    daily_used INTEGER NOT NULL DEFAULT 0,
    daily_reset_on TEXT,
    models_allow TEXT,
    expires_at TEXT,
    last_used_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(token_hash);
CREATE INDEX IF NOT EXISTS idx_call_logs_created ON call_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_call_logs_status ON call_logs(status);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS signup_items (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    secret_enc TEXT,
    mail_ready INTEGER NOT NULL DEFAULT 0,
    region TEXT,
    country TEXT,
    proxy TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    profile_json TEXT NOT NULL DEFAULT '{}',
    otp_meta_json TEXT NOT NULL DEFAULT '{}',
    payment_ref TEXT,
    payment_provider TEXT,
    payment_resumed_at TEXT,
    cookie_preview TEXT,
    cookie_blob TEXT,
    into_pool INTEGER NOT NULL DEFAULT 1,
    upstream_account_id TEXT,
    card_id TEXT,
    tries INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    message TEXT,
    csrf_meta_enc TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    activated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_signup_email ON signup_items(email);
CREATE INDEX IF NOT EXISTS idx_signup_status ON signup_items(status);

CREATE TABLE IF NOT EXISTS signup_task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signup_logs_id ON signup_task_logs(id);

CREATE TABLE IF NOT EXISTS refill_items (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    secret_enc TEXT,
    mail_ready INTEGER NOT NULL DEFAULT 0,
    region TEXT,
    into_pool INTEGER NOT NULL DEFAULT 1,
    mode TEXT NOT NULL DEFAULT 'signup',
    status TEXT NOT NULL DEFAULT 'pending',
    cookie_preview TEXT,
    upstream_account_id TEXT,
    signup_item_id TEXT,
    tries INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_refill_email ON refill_items(email);
CREATE INDEX IF NOT EXISTS idx_refill_status ON refill_items(status);

CREATE TABLE IF NOT EXISTS keepalive_jobs (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'all',
    account_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    ok_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    stop_requested INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS keepalive_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    account_id TEXT,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ka_logs_job ON keepalive_logs(job_id);

CREATE TABLE IF NOT EXISTS r2_accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    bucket TEXT NOT NULL,
    prefix TEXT NOT NULL DEFAULT 'generated',
    region TEXT NOT NULL DEFAULT 'auto',
    endpoint TEXT,
    public_url TEXT,
    access_key_id TEXT,
    secret_enc TEXT,
    quota INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0,
    objects INTEGER NOT NULL DEFAULT 0,
    uploads INTEGER NOT NULL DEFAULT 0,
    fails INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    partial INTEGER NOT NULL DEFAULT 0,
    usage_at TEXT,
    usage_err TEXT,
    last_err TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_r2_name ON r2_accounts(name);

CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    brand TEXT NOT NULL DEFAULT '',
    bin TEXT NOT NULL DEFAULT '',
    last4 TEXT NOT NULL DEFAULT '',
    pan_hash TEXT NOT NULL DEFAULT '',
    exp_month INTEGER NOT NULL DEFAULT 0,
    exp_year INTEGER NOT NULL DEFAULT 0,
    holder TEXT NOT NULL DEFAULT '',
    zip TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT 'US',
    state TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    data_enc TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'available',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    used_by_item TEXT,
    used_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_pan ON cards(pan_hash);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pool_accounts (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    email TEXT,
    name TEXT,
    password_enc TEXT,
    cookie_enc TEXT,
    fingerprint_json TEXT,
    proxy_json TEXT,
    status TEXT NOT NULL DEFAULT 'unknown',
    enabled INTEGER NOT NULL DEFAULT 1,
    ok INTEGER,
    quota_cache TEXT,
    cooldown_until INTEGER,
    daily_used INTEGER NOT NULL DEFAULT 0,
    daily_reset_on TEXT,
    e408_streak INTEGER NOT NULL DEFAULT 0,
    assigned INTEGER,
    expires_at INTEGER,
    last_used_at TEXT,
    last_keepalive_at TEXT,
    checked_at TEXT,
    use_count INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    signup_item_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pool_accounts_email ON pool_accounts(email);
CREATE INDEX IF NOT EXISTS idx_pool_accounts_enabled ON pool_accounts(enabled);

CREATE TABLE IF NOT EXISTS pool_account_meta (
    upstream_id TEXT PRIMARY KEY,
    email TEXT,
    name TEXT,
    password_enc TEXT,
    fingerprint_json TEXT,
    signup_item_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pool_meta_email ON pool_account_meta(email);
CREATE INDEX IF NOT EXISTS idx_pool_meta_signup ON pool_account_meta(signup_item_id);

CREATE TABLE IF NOT EXISTS invite_codes (
    code TEXT PRIMARY KEY,
    owner_email TEXT NOT NULL DEFAULT '',
    owner_ref TEXT NOT NULL DEFAULT '',
    max_uses INTEGER NOT NULL DEFAULT 30,
    use_count INTEGER NOT NULL DEFAULT 0,
    uses_remaining INTEGER NOT NULL DEFAULT 30,
    status TEXT NOT NULL DEFAULT 'active',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_invite_codes_status ON invite_codes(status, uses_remaining);
"""

_CALL_LOG_EXTRA = [
    ("api_type", "TEXT"),
    ("stream", "INTEGER NOT NULL DEFAULT 0"),
    ("prompt_preview", "TEXT"),
    ("upstream_task_id", "TEXT"),
    ("media_urls", "TEXT"),
    ("key_id", "INTEGER"),
    ("account", "TEXT"),
    ("account_id", "TEXT"),
    ("attempts_json", "TEXT"),
    ("log_json", "TEXT"),
]


def _sqlite_path(database_url: str) -> Path:
    if database_url.startswith("sqlite:///"):
        raw = database_url[len("sqlite:///") :]
    elif database_url.startswith("sqlite://"):
        raw = database_url[len("sqlite://") :]
    else:
        parsed = urlparse(database_url)
        raw = parsed.path or database_url
    path = Path(raw)
    if not path.is_absolute():
        from app.config import ROOT_DIR

        path = (ROOT_DIR / path).resolve()
    return path


def db_path() -> Path:
    settings = _settings_for_path()
    path = _sqlite_path(settings.database_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect() -> sqlite3.Connection:
    path = db_path()
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_call_logs(conn: sqlite3.Connection) -> None:
    cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(call_logs)").fetchall()
    }
    for name, decl in _CALL_LOG_EXTRA:
        if name not in cols:
            conn.execute(f"ALTER TABLE call_logs ADD COLUMN {name} {decl}")


def _migrate_pool_sched(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pool_accounts)").fetchall()}
    for name, ddl in (
        ("cooldown_until", "INTEGER"),
        ("daily_used", "INTEGER NOT NULL DEFAULT 0"),
        ("daily_reset_on", "TEXT"),
        ("e408_streak", "INTEGER NOT NULL DEFAULT 0"),
        ("assigned", "INTEGER"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE pool_accounts ADD COLUMN {name} {ddl}")


def _migrate_cards(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cards)").fetchall()}
    if "fail_count" not in cols:
        conn.execute("ALTER TABLE cards ADD COLUMN fail_count INTEGER NOT NULL DEFAULT 0")


def _migrate_signup_items(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(signup_items)").fetchall()}
    if "card_id" not in cols:
        conn.execute("ALTER TABLE signup_items ADD COLUMN card_id TEXT")


def init_db() -> Path:
    path = db_path()
    with get_db() as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate_call_logs(conn)
        _migrate_signup_items(conn)
        _migrate_cards(conn)
        _migrate_pool_sched(conn)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now')",
            ("schema_version", "11"),
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now')",
            ("app", "muse-proxy"),
        )
    return path
