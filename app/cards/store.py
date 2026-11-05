"""Card pool store — encrypted PAN/CVV at rest, masked in every API response.

Import format (one card per line, '|' or TAB delimited)::

    PAN|MM/YY|CVV|Holder|Address|City|State|ZIP|Country|Phone|Email|IP|Live|Code: 85|=> url

Short forms are accepted too: ``PAN|MM/YY|CVV|ZIP|US`` and
``PAN,MM/YY,CVV,...`` (comma only when there is no '|' in the line).

Lifecycle: ``available → in_use → used`` on success, ``→ failed`` on a soft
decline (retried later) or ``→ dead`` on a hard decline / wrong card data.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any

from app.db import get_db
from app.signup.crypto import decrypt_dict, encrypt_dict

STATUSES = ("available", "in_use", "used", "failed", "dead", "disabled")
_ACTIVE = ("available",)

_COUNTRY_MAP = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "canada": "CA",
    "ca": "CA",
    "united kingdom": "GB",
    "uk": "GB",
    "gb": "GB",
    "great britain": "GB",
    "australia": "AU",
    "au": "AU",
    "germany": "DE",
    "de": "DE",
    "france": "FR",
    "fr": "FR",
    "japan": "JP",
    "jp": "JP",
    "singapore": "SG",
    "sg": "SG",
    "china": "CN",
    "cn": "CN",
}

_DEAD_WORDS = ("dead", "die", "declined", "invalid", "bad", "fail", "error")
_LIVE_WORDS = ("live", "valid", "ok", "good", "approved", "works")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id() -> str:
    return "c_" + uuid.uuid4().hex[:12]


def _digits(v: Any) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _brand(pan: str) -> str:
    if not pan:
        return ""
    if pan[0] == "4":
        return "Visa"
    if pan[:2] in ("51", "52", "53", "54", "55") or (pan[:2].isdigit() and 2221 <= int(pan[:4] or 0) <= 2720):
        return "Mastercard"
    if pan[:2] in ("34", "37"):
        return "Amex"
    if pan[:2] in ("60", "65") or pan[:4] == "6011":
        return "Discover"
    if pan[:2] == "62":
        return "UnionPay"
    if pan[:2] == "35":
        return "JCB"
    return "Card"


def _pan_hash(pan: str) -> str:
    return hashlib.sha256(pan.encode("ascii", "ignore")).hexdigest()


def _norm_country(value: str) -> str:
    raw = (value or "").strip()
    low = raw.lower()
    if low in _COUNTRY_MAP:
        return _COUNTRY_MAP[low]
    if len(raw) == 2 and raw.isalpha():
        return raw.upper()
    return "US"


def _parse_expiry(value: str) -> tuple[int, int] | None:
    raw = (value or "").strip()
    m = re.match(r"^(\d{1,2})\s*[/\-\s]\s*(\d{2}|\d{4})$", raw)
    if not m:
        m2 = re.match(r"^(\d{2})(\d{2})$", raw)
        if not m2:
            return None
        month, year = int(m2.group(1)), int(m2.group(2))
    else:
        month, year = int(m.group(1)), int(m.group(2))
        if year > 100:
            year = year % 100
    if not (1 <= month <= 12):
        return None
    return month, 2000 + year


def _split_line(line: str) -> list[str]:
    if "|" in line:
        return [p.strip() for p in line.split("|")]
    if "\t" in line:
        return [p.strip() for p in line.split("\t")]
    if "----" in line:
        return [p.strip() for p in line.split("----")]
    return [p.strip() for p in line.split(",")]


def parse_import_text(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse pasted card text. Returns (cards, errors)."""
    cards: list[dict[str, Any]] = []
    errors: list[str] = []
    for lineno, raw_line in enumerate((text or "").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = _split_line(line)
        if len(parts) < 3:
            errors.append(f"第 {lineno} 行：字段不足（需 卡号|有效期|CVV…）")
            continue
        pan = _digits(parts[0])
        if not (13 <= len(pan) <= 19):
            errors.append(f"第 {lineno} 行：卡号无效")
            continue
        exp = _parse_expiry(parts[1] if len(parts) > 1 else "")
        if not exp:
            errors.append(f"第 {lineno} 行：有效期无法解析（需 MM/YY）")
            continue
        cvv = _digits(parts[2] if len(parts) > 2 else "")
        if not (3 <= len(cvv) <= 4):
            errors.append(f"第 {lineno} 行：CVV 无效")
            continue

        def pick(idx: int) -> str:
            return parts[idx] if len(parts) > idx else ""

        # Long form: holder,address,city,state,zip,country,phone,email,ip
        # Short form: holder,zip,country
        holder = pick(3)
        if len(parts) >= 10:
            address, city, state = pick(4), pick(5), pick(6)
            zip_code, country, phone, email, ip = pick(7), pick(8), pick(9), pick(10), pick(11)
            status_cell = " ".join(parts[12:]).lower()
        else:
            address = city = state = ""
            zip_code, country = pick(4), pick(5)
            phone = email = ip = ""
            status_cell = " ".join(parts[6:]).lower()

        status = "available"
        if any(w in status_cell for w in _DEAD_WORDS):
            status = "dead"
        elif any(w in status_cell for w in _LIVE_WORDS) or not status_cell:
            status = "available"

        cards.append(
            {
                "pan": pan,
                "exp_month": exp[0],
                "exp_year": exp[1],
                "cvv": cvv,
                "holder": holder,
                "address": address,
                "city": city,
                "state": state,
                "zip": zip_code,
                "country": _norm_country(country),
                "phone": phone,
                "email": email,
                "ip": ip,
                "brand": _brand(pan),
                "bin": pan[:6],
                "last4": pan[-4:],
                "status": status,
            }
        )
    return cards, errors


def _is_expired(month: int, year: int) -> bool:
    if not month or not year:
        return False
    now = time.gmtime()
    return (year, month) < (now.tm_year, now.tm_mon)


def import_cards(text: str, *, source: str = "") -> dict[str, Any]:
    cards, errors = parse_import_text(text)
    added = dup = 0
    with get_db() as conn:
        for card in cards:
            ph = _pan_hash(card["pan"])
            exists = conn.execute("SELECT id FROM cards WHERE pan_hash=?", (ph,)).fetchone()
            if exists:
                dup += 1
                continue
            now = _now()
            secret = {
                k: card[k]
                for k in (
                    "pan",
                    "cvv",
                    "holder",
                    "address",
                    "city",
                    "state",
                    "zip",
                    "country",
                    "phone",
                    "email",
                    "ip",
                )
            }
            conn.execute(
                """INSERT INTO cards(
                    id, brand, bin, last4, pan_hash, exp_month, exp_year, holder,
                    zip, country, state, city, phone, email, ip, source,
                    data_enc, status, attempts, note, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _new_id(),
                    card["brand"],
                    card["bin"],
                    card["last4"],
                    ph,
                    card["exp_month"],
                    card["exp_year"],
                    card["holder"],
                    card["zip"],
                    card["country"],
                    card["state"],
                    card["city"],
                    card["phone"],
                    card["email"],
                    card["ip"],
                    source,
                    encrypt_dict(secret),
                    card["status"],
                    0,
                    "",
                    now,
                    now,
                ),
            )
            added += 1
    return {"added": added, "duplicate": dup, "invalid": len(errors), "errors": errors[:20]}


def row_to_public(row: Any, *, reveal: bool = False) -> dict[str, Any]:
    d = dict(row)
    brand = d.get("brand") or _brand("")
    last4 = d.get("last4") or ""
    out = {
        "id": d["id"],
        "brand": brand,
        "bin": d.get("bin") or "",
        "last4": last4,
        "masked": f"{brand} •••• {last4}".strip() if last4 else "••••",
        "exp_month": d.get("exp_month") or 0,
        "expiry": f"{int(d.get('exp_month') or 0):02d}/{str(d.get('exp_year') or '')[-2:]}",
        "exp_year": d.get("exp_year") or 0,
        "holder": d.get("holder") or "",
        "zip": d.get("zip") or "",
        "country": d.get("country") or "",
        "state": d.get("state") or "",
        "city": d.get("city") or "",
        "status": d.get("status") or "available",
        "expired": _is_expired(int(d.get("exp_month") or 0), int(d.get("exp_year") or 0)),
        "attempts": d.get("attempts") or 0,
        "fail_count": d.get("fail_count") or 0,
        "last_error": d.get("last_error"),
        "used_by_item": d.get("used_by_item"),
        "used_at": d.get("used_at"),
        "note": d.get("note") or "",
        "source": d.get("source") or "",
        "has_cvv": True,
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }
    if reveal:
        out["secret"] = decrypt_dict(d.get("data_enc"))
    return out


def list_cards(
    *,
    status: str = "all",
    q: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    clauses: list[str] = []
    args: list[Any] = []
    if status and status != "all":
        if status == "expired":
            now = time.gmtime()
            clauses.append(
                "(exp_year < ? OR (exp_year = ? AND exp_month < ?))"
            )
            args.extend([now.tm_year, now.tm_year, now.tm_mon])
        elif status == "available":
            now = time.gmtime()
            clauses.append("status='available'")
            clauses.append("(exp_year > ? OR (exp_year = ? AND exp_month >= ?))")
            args.extend([now.tm_year, now.tm_year, now.tm_mon])
        else:
            clauses.append("status=?")
            args.append(status)
    if q.strip():
        like = f"%{q.strip()}%"
        clauses.append(
            "(last4 LIKE ? OR holder LIKE ? OR bin LIKE ? OR zip LIKE ? OR note LIKE ? OR id LIKE ?)"
        )
        args.extend([like] * 6)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM cards {where}", args).fetchone()["c"]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(max(1, page), pages)
        rows = conn.execute(
            f"SELECT * FROM cards {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            args + [page_size, (page - 1) * page_size],
        ).fetchall()
        stats_rows = conn.execute("SELECT status, COUNT(*) AS c FROM cards GROUP BY status").fetchall()
    return {
        "items": [row_to_public(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "stats": {r["status"]: r["c"] for r in stats_rows},
    }


def get_card(card_id: str, *, reveal: bool = False) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    return row_to_public(row, reveal=reveal) if row else None


def get_card_secret(card_id: str) -> dict[str, Any]:
    """Full card material (PAN/CVV included) — only for the checkout client."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    if not row:
        return {}
    secret = decrypt_dict(row["data_enc"]) or {}
    secret.update(
        {
            "id": row["id"],
            "brand": row["brand"],
            "bin": row["bin"],
            "last4": row["last4"],
            "exp_month": row["exp_month"],
            "exp_year": row["exp_year"],
            "status": row["status"],
        }
    )
    return secret


def claim_card(item_id: str = "") -> dict[str, Any] | None:
    """Atomically pick one usable card and mark it in_use."""
    now_g = time.gmtime()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE status='available' "
            "ORDER BY fail_count ASC, created_at ASC LIMIT 200"
        ).fetchall()
        for row in rows:
            if _is_expired(int(row["exp_month"] or 0), int(row["exp_year"] or 0)):
                conn.execute(
                    "UPDATE cards SET status='dead', last_error='已过期', updated_at=? WHERE id=?",
                    (_now(), row["id"]),
                )
                continue
            cur = conn.execute(
                "UPDATE cards SET status='in_use', attempts=attempts+1, updated_at=?, "
                "used_by_item=? WHERE id=? AND status='available'",
                (_now(), item_id or None, row["id"]),
            )
            if cur.rowcount == 1:
                secret = decrypt_dict(row["data_enc"]) or {}
                secret.update(
                    {
                        "id": row["id"],
                        "brand": row["brand"],
                        "bin": row["bin"],
                        "last4": row["last4"],
                        "exp_month": row["exp_month"],
                        "exp_year": row["exp_year"],
                        "holder": row["holder"],
                        "zip": row["zip"],
                        "country": row["country"],
                        "state": row["state"],
                        "city": row["city"],
                        "attempts": (row["attempts"] or 0) + 1,
                    }
                )
                return secret
    return None


def claim_card_by_id(card_id: str, item_id: str = "") -> dict[str, Any] | None:
    """Claim one explicitly chosen card (status must be available or failed)."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        if not row:
            return None
        if _is_expired(int(row["exp_month"] or 0), int(row["exp_year"] or 0)):
            conn.execute(
                "UPDATE cards SET status='dead', last_error='已过期', updated_at=? WHERE id=?",
                (_now(), card_id),
            )
            return None
        if row["status"] not in ("available", "failed"):
            return None
        conn.execute(
            "UPDATE cards SET status='in_use', attempts=attempts+1, last_error=NULL, "
            "used_by_item=?, updated_at=? WHERE id=?",
            (item_id or None, _now(), card_id),
        )
        secret = decrypt_dict(row["data_enc"]) or {}
    secret.update(
        {
            "id": row["id"],
            "brand": row["brand"],
            "bin": row["bin"],
            "last4": row["last4"],
            "exp_month": row["exp_month"],
            "exp_year": row["exp_year"],
            "holder": row["holder"],
            "zip": row["zip"],
            "country": row["country"],
            "state": row["state"],
            "city": row["city"],
            "attempts": (row["attempts"] or 0) + 1,
        }
    )
    return secret


def release_card(card_id: str, *, status: str = "available", error: str | None = None) -> None:
    if status not in STATUSES:
        status = "available"
    with get_db() as conn:
        conn.execute(
            "UPDATE cards SET status=?, last_error=?, used_by_item=NULL, updated_at=? WHERE id=?",
            (status, (error or "")[:500] or None, _now(), card_id),
        )


def mark_used(card_id: str, item_id: str = "") -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE cards SET status='used', used_by_item=?, used_at=?, last_error=NULL, "
            "updated_at=? WHERE id=?",
            (item_id or None, _now(), _now(), card_id),
        )


MAX_CARD_FAILURES = 3


def record_failure(
    card_id: str, error: str, *, hard: bool = False, item_id: str = ""
) -> str:
    """卡可重复使用：失败后回到 available；失败次数 > 3 自动禁用。

    返回新状态（available / disabled / dead）。
    """
    with get_db() as conn:
        row = conn.execute("SELECT fail_count FROM cards WHERE id=?", (card_id,)).fetchone()
        if not row:
            return "missing"
        fails = int(row["fail_count"] or 0) + 1
        if hard:
            status = "dead"
        elif fails > MAX_CARD_FAILURES:
            status = "disabled"
        else:
            status = "available"
        conn.execute(
            "UPDATE cards SET status=?, fail_count=?, last_error=?, used_by_item=?, updated_at=? "
            "WHERE id=?",
            (status, fails, (error or "")[:500], item_id or None, _now(), card_id),
        )
        return status


def mark_failed(card_id: str, error: str, *, hard: bool = False, item_id: str = "") -> None:
    record_failure(card_id, error, hard=hard, item_id=item_id)


def update_card(card_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        if not row:
            return None
        secret = decrypt_dict(row["data_enc"]) or {}
        fields: dict[str, Any] = {}
        for key in ("holder", "zip", "country", "state", "city", "phone", "email", "note"):
            if patch.get(key) is not None:
                fields[key] = str(patch[key])
                if key in secret:
                    secret[key] = str(patch[key])
        if patch.get("status") in STATUSES:
            fields["status"] = patch["status"]
        if patch.get("exp_month") is not None:
            fields["exp_month"] = int(patch["exp_month"])
        if patch.get("exp_year") is not None:
            year = int(patch["exp_year"])
            fields["exp_year"] = year + 2000 if year < 100 else year
        pan = _digits(patch.get("pan") or "")
        if pan:
            if not (13 <= len(pan) <= 19):
                raise ValueError("卡号无效")
            fields.update(
                pan_hash=_pan_hash(pan),
                last4=pan[-4:],
                bin=pan[:6],
                brand=_brand(pan),
            )
            secret["pan"] = pan
        cvv = _digits(patch.get("cvv") or "")
        if cvv:
            if not (3 <= len(cvv) <= 4):
                raise ValueError("CVV 无效")
            secret["cvv"] = cvv
        fields["data_enc"] = encrypt_dict(secret)
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE cards SET {cols} WHERE id=?", list(fields.values()) + [card_id])
    return get_card(card_id)


def delete_cards(ids: list[str]) -> int:
    if not ids:
        return 0
    with get_db() as conn:
        q = ",".join("?" * len(ids))
        cur = conn.execute(f"DELETE FROM cards WHERE id IN ({q})", ids)
        return cur.rowcount


def reset_cards(ids: list[str]) -> int:
    if not ids:
        return 0
    with get_db() as conn:
        q = ",".join("?" * len(ids))
        cur = conn.execute(
            f"UPDATE cards SET status='available', last_error=NULL, used_by_item=NULL, fail_count=0, "
            f"updated_at=? WHERE id IN ({q}) AND status IN ('failed','dead','disabled','used','in_use')",
            [_now()] + ids,
        )
        return cur.rowcount


def set_enabled(ids: list[str], enabled: bool) -> int:
    if not ids:
        return 0
    with get_db() as conn:
        q = ",".join("?" * len(ids))
        if enabled:
            cur = conn.execute(
                f"UPDATE cards SET status='available', updated_at=? WHERE id IN ({q}) "
                f"AND status IN ('disabled','failed','dead')",
                [_now()] + ids,
            )
        else:
            cur = conn.execute(
                f"UPDATE cards SET status='disabled', updated_at=? WHERE id IN ({q}) "
                f"AND status IN ('available','failed','dead')",
                [_now()] + ids,
            )
        return cur.rowcount


def export_text(ids: list[str] | None = None, *, full: bool = False) -> str:
    with get_db() as conn:
        if ids:
            q = ",".join("?" * len(ids))
            rows = conn.execute(f"SELECT * FROM cards WHERE id IN ({q})", ids).fetchall()
        else:
            rows = conn.execute("SELECT * FROM cards ORDER BY created_at").fetchall()
    lines: list[str] = []
    for row in rows:
        secret = decrypt_dict(row["data_enc"]) or {}
        pan = secret.get("pan") or ""
        cvv = secret.get("cvv") or ""
        if not full:
            pan = f"{row['bin']}xxxxxx{row['last4']}"
            cvv = "***"
        lines.append(
            "|".join(
                [
                    pan,
                    f"{int(row['exp_month']):02d}/{str(row['exp_year'])[-2:]}",
                    cvv,
                    row["holder"] or "",
                    secret.get("address") or "",
                    secret.get("city") or "",
                    row["state"] or "",
                    row["zip"] or "",
                    row["country"] or "",
                    secret.get("phone") or "",
                    secret.get("email") or "",
                    secret.get("ip") or "",
                    row["status"],
                ]
            )
        )
    return "\n".join(lines)


def stats() -> dict[str, Any]:
    with get_db() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS c FROM cards GROUP BY status").fetchall()
        total = conn.execute("SELECT COUNT(*) AS c FROM cards").fetchone()["c"]
    out = {r["status"]: r["c"] for r in rows}
    out["total"] = total
    return out
