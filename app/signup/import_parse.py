"""Parse 小AD-style email credential lines (6-segment / 4-segment)."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class MailCreds:
    email: str
    password: str
    client_id: str
    refresh_token: str
    aux_email: str = ""
    aux_password: str = ""

    @property
    def mail_ready(self) -> bool:
        # 4-seg: email+password+client+refresh; 6-seg adds aux (preferred for OTP)
        base = bool(self.email and self.password and self.client_id and self.refresh_token)
        return base


_SPLIT = re.compile(r"\t|\|")


def _parts(line: str) -> list[str]:
    line = line.strip()
    if not line or line.startswith("#"):
        return []
    if "----" in line:
        return [p.strip() for p in line.split("----")]
    if "|" in line or "\t" in line:
        return [p.strip() for p in _SPLIT.split(line)]
    return [line]


def parse_import_line(line: str) -> MailCreds | None:
    parts = _parts(line)
    if len(parts) < 4:
        return None
    email = parts[0]
    if "@" not in email:
        return None
    password = parts[1]
    client_id = parts[2]
    refresh = parts[3]
    aux_email = parts[4] if len(parts) >= 5 else ""
    aux_password = parts[5] if len(parts) >= 6 else ""
    return MailCreds(
        email=email,
        password=password,
        client_id=client_id,
        refresh_token=refresh,
        aux_email=aux_email,
        aux_password=aux_password,
    )


def parse_import_text(text: str) -> list[MailCreds]:
    out: list[MailCreds] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        c = parse_import_line(raw)
        if not c:
            continue
        key = c.email.lower()
        if key in seen:
            # last wins within same paste
            out = [x for x in out if x.email.lower() != key]
        seen.add(key)
        out.append(c)
    return out


def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"
