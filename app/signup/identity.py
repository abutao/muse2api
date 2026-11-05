"""Random signup identity helpers (name / DOB / login password).

Aligned with 小 AD 补号思路：资料与设密由服务端随机填充，不依赖固定假数据；
密码写入加密 secret，不写进前端日志。
"""

from __future__ import annotations

import secrets
import string
from datetime import date, timedelta
from typing import Any


_FIRST_NAMES = [
    "James", "Emma", "Liam", "Olivia", "Noah", "Ava", "William", "Sophia",
    "Benjamin", "Isabella", "Lucas", "Mia", "Henry", "Charlotte", "Alexander",
    "Amelia", "Michael", "Harper", "Daniel", "Evelyn", "Matthew", "Abigail",
    "Samuel", "Emily", "David", "Elizabeth", "Joseph", "Sofia", "Thomas",
    "Avery", "Charles", "Ella", "Christopher", "Scarlett", "Andrew", "Grace",
    "Joshua", "Chloe", "Nathan", "Victoria", "Ryan", "Riley", "Jason", "Zoe",
]

_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King",
    "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green", "Adams",
]


def random_first_name() -> str:
    return secrets.choice(_FIRST_NAMES)


def random_last_name() -> str:
    return secrets.choice(_LAST_NAMES)


def random_display_name(first: str | None = None, last: str | None = None) -> str:
    f = first or random_first_name()
    l = last or random_last_name()
    return f"{f} {l}"


def random_date_of_birth(*, min_age: int = 21, max_age: int = 55) -> str:
    """Compliant adult DOB as YYYY-MM-DD (uniform across age window)."""
    if min_age < 18:
        min_age = 18
    if max_age < min_age:
        max_age = min_age
    today = date.today()
    # inclusive age window
    youngest = today - timedelta(days=365 * min_age + 30)
    oldest = today - timedelta(days=365 * max_age + 200)
    # oldest is earlier calendar date
    start = oldest.toordinal()
    end = youngest.toordinal()
    picked = date.fromordinal(secrets.randbelow(end - start + 1) + start)
    return picked.isoformat()


def random_password(*, length: int = 16) -> str:
    """Strong password: upper/lower/digit/symbol, no ambiguous lookalikes omitted lightly."""
    if length < 10:
        length = 10
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*_-+=?"
    # guarantee class coverage
    parts = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*_-+=?"),
    ]
    parts += [secrets.choice(alphabet) for _ in range(length - len(parts))]
    # shuffle
    for i in range(len(parts) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        parts[i], parts[j] = parts[j], parts[i]
    return "".join(parts)


def ensure_profile_identity(profile: dict[str, Any] | None) -> dict[str, Any]:
    """Fill missing first/last name and date_of_birth in place; return profile."""
    p = dict(profile or {})
    if not p.get("first_name"):
        p["first_name"] = random_first_name()
    if not p.get("last_name"):
        p["last_name"] = random_last_name()
    if not p.get("display_name"):
        p["display_name"] = f"{p['first_name']} {p['last_name']}"
    if not p.get("date_of_birth"):
        # accept birthday alias later in machine
        p["date_of_birth"] = random_date_of_birth()
    if "remember_device" not in p:
        p["remember_device"] = True
    return p


def password_preview(password: str) -> str:
    if not password:
        return ""
    if len(password) <= 4:
        return "****"
    return password[:2] + "…" + password[-2:]
