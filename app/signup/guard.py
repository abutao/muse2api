"""Reject card / payment-secret fields (hard ban)."""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException

# Exact keys and prefixes that must never appear on signup APIs / DB writes.
_EXACT = {
    "card_number",
    "pan",
    "cvv",
    "cvc",
    "expiry",
    "exp_month",
    "exp_year",
    "expiry_month",
    "expiry_year",
    "card_holder",
    "cardholder",
    "cardholder_name",
    "billing_address",
    "ptt",
    "jwe",
    "platform_trust_token",
    "payment_method_id",  # card-backed method ids — ban; payment_ref is OK
    "card",
}
_PREFIXES = ("card_", "cc_", "credit_card")


def _walk(obj: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if v is None:
                continue
            key = str(k)
            low = key.lower()
            here = f"{path}.{key}" if path else key
            if low in _EXACT or any(low.startswith(p) for p in _PREFIXES):
                hits.append(here)
                continue  # no need to walk into banned subtree
            hits.extend(_walk(v, here))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hits.extend(_walk(v, f"{path}[{i}]"))
    return hits


def reject_card_fields(payload: Any) -> None:
    """Raise HTTP 400 if any banned card/payment-secret field is present."""
    hits = _walk(payload)
    # payment_ref / provider / note are allowed; filter false positives if any
    hits = [h for h in hits if h.split(".")[-1].lower() not in {"payment_ref", "provider", "note"}]
    if hits:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "card_fields_forbidden",
                "message": "卡密/代扣字段禁止；仅允许 payment_ref 等外部引用",
                "fields": hits[:20],
            },
        )
