"""Local invite-code library.

Successful accounts each own one Muse invite code (about 30 uses). New
registrations redeem a code from this library so hatch admits them instead of
leaving them geo-gated. Counts are reserved locally when a code is handed out,
and given back when the redeem call never reached the server.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db import connect, get_db


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def csrf_from_cookies(cookies: dict[str, str] | None) -> str:
    """Header token is the 43-char prefix; the cookie appends a signature."""
    raw = str((cookies or {}).get("hatch_native_auth_csrf") or "").strip()
    head = raw.split(".", 1)[0].strip()
    if len(head) < 20:
        return ""
    return head


def parse_invite_payload(data: Any) -> dict[str, Any] | None:
    """Pull code and remaining uses out of GET/POST /api/hatch/invite."""
    if not isinstance(data, dict):
        return None
    code = str(data.get("code") or data.get("inviteCode") or "").strip().upper()
    if not code or code in {"NONE", "NULL"}:
        return None
    max_uses = _as_int(data.get("max_uses", data.get("maxUses")), 30)
    if data.get("use_count") is not None or data.get("useCount") is not None:
        use_count = _as_int(
            data.get("use_count") if data.get("use_count") is not None else data.get("useCount"),
            0,
        )
    else:
        use_count = 0
    if data.get("uses_remaining") is not None or data.get("usesRemaining") is not None:
        remaining = _as_int(
            data.get("uses_remaining")
            if data.get("uses_remaining") is not None
            else data.get("usesRemaining"),
            0,
        )
    else:
        remaining = max(0, max_uses - use_count)
    return {
        "code": code,
        "max_uses": max(0, max_uses),
        "use_count": max(0, use_count),
        "uses_remaining": max(0, remaining),
    }


def classify_redeem_error(status_code: int, message: str, detail: Any) -> str:
    """already: this account is already admitted. dead: code is spent. retry: transport."""
    blob = f"{status_code} {message} {detail}".lower()
    if any(k in blob for k in ("already", "redeemed")) and "invalid" not in blob:
        return "already"
    dead_keys = (
        "invalid",
        "exhaust",
        "not found",
        "notfound",
        "unknown code",
        "max_uses",
        "max uses",
        "used up",
        "no uses",
        "no remaining",
    )
    if status_code in (400, 404, 409, 410) or any(k in blob for k in dead_keys):
        return "dead"
    return "retry"


def remember(payload: Any, *, owner_email: str = "", owner_ref: str = "") -> str | None:
    parsed = parse_invite_payload(payload)
    if not parsed:
        return None
    status = "exhausted" if parsed["uses_remaining"] <= 0 else "active"
    now = _now()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO invite_codes(
                code, owner_email, owner_ref, max_uses, use_count, uses_remaining, status, updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(code) DO UPDATE SET
                owner_email=CASE WHEN excluded.owner_email!='' THEN excluded.owner_email ELSE invite_codes.owner_email END,
                owner_ref=CASE WHEN excluded.owner_ref!='' THEN excluded.owner_ref ELSE invite_codes.owner_ref END,
                max_uses=excluded.max_uses,
                use_count=MAX(invite_codes.use_count, excluded.use_count),
                uses_remaining=MIN(invite_codes.uses_remaining, excluded.uses_remaining),
                status=CASE
                    WHEN invite_codes.status='invalid' THEN 'invalid'
                    WHEN MIN(invite_codes.uses_remaining, excluded.uses_remaining)<=0 THEN 'exhausted'
                    ELSE 'active'
                END,
                updated_at=excluded.updated_at
            """,
            (
                parsed["code"],
                (owner_email or "").strip(),
                (owner_ref or "").strip(),
                parsed["max_uses"],
                parsed["use_count"],
                parsed["uses_remaining"],
                status,
                now,
            ),
        )
    return parsed["code"]


def slots(exclude_email: str = "") -> int:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(uses_remaining), 0) AS n
            FROM invite_codes
            WHERE status='active' AND uses_remaining>0
              AND lower(owner_email)!=lower(?)
            """,
            ((exclude_email or "").strip(),),
        ).fetchone()
    return int(row["n"] or 0) if row else 0


def claim(exclude_email: str = "") -> str | None:
    """Reserve one use. Caller must release() if the redeem never reached Muse."""
    email = (exclude_email or "").strip()
    conn = connect()
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT code FROM invite_codes
            WHERE status='active' AND uses_remaining>0
              AND lower(owner_email)!=lower(?)
            ORDER BY uses_remaining DESC, updated_at ASC
            LIMIT 1
            """,
            (email,),
        ).fetchone()
        if not row:
            conn.execute("COMMIT")
            return None
        code = str(row["code"])
        conn.execute(
            """
            UPDATE invite_codes
            SET uses_remaining=uses_remaining-1,
                use_count=use_count+1,
                status=CASE WHEN uses_remaining-1<=0 THEN 'exhausted' ELSE 'active' END,
                updated_at=?
            WHERE code=?
            """,
            (_now(), code),
        )
        conn.execute("COMMIT")
        return code
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def release(code: str) -> None:
    if not code:
        return
    with get_db() as conn:
        conn.execute(
            """
            UPDATE invite_codes
            SET uses_remaining=uses_remaining+1,
                use_count=CASE WHEN use_count>0 THEN use_count-1 ELSE 0 END,
                status=CASE WHEN status='invalid' THEN 'invalid' ELSE 'active' END,
                updated_at=?
            WHERE code=?
            """,
            (_now(), code.strip().upper()),
        )


def burn(code: str) -> None:
    if not code:
        return
    with get_db() as conn:
        conn.execute(
            "UPDATE invite_codes SET status='invalid', uses_remaining=0, updated_at=? WHERE code=?",
            (_now(), code.strip().upper()),
        )


def known_owner_keys() -> set[str]:
    """Owners that already have a row in the invite library."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT owner_email, owner_ref FROM invite_codes WHERE code != ''"
        ).fetchall()
    keys: set[str] = set()
    for row in rows:
        email = str(row["owner_email"] or "").strip().lower()
        ref = str(row["owner_ref"] or "").strip()
        if email:
            keys.add("email:" + email)
        if ref:
            keys.add("ref:" + ref)
    return keys


def donor_rows(limit: int | None = None) -> list[dict[str, str]]:
    """Pooled signup sessions first, then pool accounts that only have cookies."""
    cap = None
    if limit:
        cap = max(1, min(int(limit), 500))
    with get_db() as conn:
        item_sql = """
            SELECT id, email FROM signup_items
            WHERE status IN ('pooled', 'activated')
              AND csrf_meta_enc IS NOT NULL AND length(csrf_meta_enc) > 20
            ORDER BY COALESCE(activated_at, updated_at) DESC
        """
        pool_sql = """
            SELECT id, email, signup_item_id FROM pool_accounts
            WHERE enabled=1 AND length(COALESCE(cookie_enc, '')) > 20
            ORDER BY updated_at DESC
        """
        if cap is None:
            items = conn.execute(item_sql).fetchall()
            pools = conn.execute(pool_sql).fetchall()
        else:
            items = conn.execute(item_sql + " LIMIT ?", (cap,)).fetchall()
            pools = conn.execute(pool_sql + " LIMIT ?", (cap,)).fetchall()
    out: list[dict[str, str]] = [
        {"kind": "item", "id": str(r["id"]), "email": str(r["email"] or "")} for r in items
    ]
    seen = {row["email"].lower() for row in out if row["email"]}
    seen_items = {row["id"] for row in out}
    for r in pools:
        email = str(r["email"] or "")
        signup_id = str(r["signup_item_id"] or "")
        if email and email.lower() in seen:
            continue
        if signup_id and signup_id in seen_items:
            continue
        out.append(
            {
                "kind": "pool",
                "id": str(r["id"]),
                "email": email,
                "signup_item_id": signup_id,
            }
        )
    return out


def missing_donors(exclude_email: str = "") -> list[dict[str, str]]:
    """Successful accounts whose invite code is not in the library yet."""
    known = known_owner_keys()
    skip = (exclude_email or "").strip().lower()
    pending: list[dict[str, str]] = []
    for donor in donor_rows():
        email = (donor.get("email") or "").strip().lower()
        if skip and email == skip:
            continue
        if f"ref:{donor.get('id') or ''}" in known:
            continue
        if email and f"email:{email}" in known:
            continue
        signup_id = donor.get("signup_item_id") or ""
        if signup_id and f"ref:{signup_id}" in known:
            continue
        pending.append(donor)
    return pending
