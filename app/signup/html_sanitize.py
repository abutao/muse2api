"""Sanitize HTML for inbox preview (no script / handlers)."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser


_ALLOWED = {
    "a",
    "b",
    "br",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
_ATTR = {"href", "title", "class"}


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in _ALLOWED:
            return
        safe = []
        for k, v in attrs:
            k = (k or "").lower()
            if k not in _ATTR or v is None:
                continue
            if k == "href" and not re.match(r"^(https?:|mailto:|#)", v, re.I):
                continue
            safe.append(f'{k}="{html.escape(v, quote=True)}"')
        self._out.append("<" + tag + ((" " + " ".join(safe)) if safe else "") + ">")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _ALLOWED and tag != "br":
            self._out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self._out.append(html.escape(data))

    def handle_entityref(self, name: str) -> None:
        self._out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._out.append(f"&#{name};")

    def result(self) -> str:
        return "".join(self._out)


def sanitize_html(raw: str | None) -> str:
    if not raw:
        return ""
    # Strip script/style blocks first
    text = re.sub(r"(?is)<(script|style|iframe|object|embed)[^>]*>.*?</\1>", "", raw)
    p = _Sanitizer()
    try:
        p.feed(text)
        p.close()
    except Exception:
        return html.escape(re.sub(r"<[^>]+>", "", raw))
    return p.result()
