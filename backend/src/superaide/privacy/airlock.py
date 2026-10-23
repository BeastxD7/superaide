"""Airlock (#128): other people's data is redacted before anything is uploaded to a model.

The pure-code half of the on-device layer. It is a deterministic pass, no model and no network,
so it can run in the browser or phone later and runs on the server today as a second line:
- contact details of anyone (e-mail addresses, phone numbers, @handles), payment cards (Luhn
  checked), IBANs, national-ID-like numbers and IP addresses become stable placeholders such as
  "[email 1]"; the same value always gets the same placeholder inside one pass;
- names the caller lists (the owner's contacts, people named in a shared screenshot) become
  "[person 1]" with the same rule;
- values the caller lists as the owner's own (`keep`) are left alone.
The placeholder to original map stays with the caller and is never part of the output, so the
upload carries no original. `restore` puts originals back into a reply. `preview` is what the
owner sees before sending: kinds and counts, never the values. Over-redacting is the safe error.

What this does not do: read faces or text in images, find a name nobody listed, or prove where
the pass ran. Those need the on-device model and a device attestation, which are not built.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
HANDLE = re.compile(r"(?<![\w@.])@[A-Za-z][\w.]{2,30}\b")
PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{6,}\d(?![\w])")
CARD = re.compile(r"(?<![\w.])(?:\d[ -]?){13,19}(?![\w])")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
NATIONAL_ID = re.compile(r"(?<![\w.])\d{3}-\d{2}-\d{4}(?![\w])")
IPV4 = re.compile(r"(?<![\w.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\w.])")
DATE_SHAPE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$|^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$")

# Order matters: the narrower shapes win over the broader digit runs.
DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", EMAIL),
    ("iban", IBAN),
    ("national id", NATIONAL_ID),
    ("card", CARD),
    ("ip address", IPV4),
    ("phone", PHONE),
    ("handle", HANDLE),
)


def luhn(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if flip:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        flip = not flip
    return total % 10 == 0


@dataclass
class Redaction:
    text: str
    found: dict[str, int] = field(default_factory=dict)  # kind -> how many (not distinct values)
    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> original; stays with the caller

    @property
    def changed(self) -> bool:
        return bool(self.found)

    def preview(self) -> dict[str, Any]:
        """What the owner is shown before an upload: what kinds were taken out, never the values."""
        return {"redacted": sum(self.found.values()), "kinds": dict(self.found)}


class _Pass:
    def __init__(self, keep: Iterable[str]) -> None:
        self.keep = {k.casefold() for k in keep if k}
        self.numbers: dict[tuple[str, str], str] = {}
        self.per_kind: dict[str, int] = {}
        self.found: dict[str, int] = {}
        self.mapping: dict[str, str] = {}

    def placeholder(self, kind: str, value: str) -> str:
        key = (kind, value.casefold())
        if key not in self.numbers:
            self.per_kind[kind] = self.per_kind.get(kind, 0) + 1
            self.numbers[key] = f"[{kind} {self.per_kind[kind]}]"
            self.mapping[self.numbers[key]] = value
        self.found[kind] = self.found.get(kind, 0) + 1
        return self.numbers[key]

    def replace(self, kind: str, pattern: re.Pattern[str], text: str) -> str:
        def sub(m: re.Match[str]) -> str:
            value = m.group(0)
            if value.casefold() in self.keep or value.strip().casefold() in self.keep:
                return value
            digits = re.sub(r"\D", "", value)
            if kind == "card" and not (13 <= len(digits) <= 19 and luhn(digits)):
                return value
            if kind == "phone" and (
                DATE_SHAPE.match(value.strip())
                or not 8 <= len(digits) <= 15
                or value.strip().isdigit()  # a bare run of digits is an order number or an id, not a phone
            ):
                return value
            if kind == "handle" and value.casefold() in self.keep:
                return value
            return self.placeholder(kind, value.strip())

        return pattern.sub(sub, text)


def _run(p: _Pass, text: str, names: list[str]) -> str:
    # Shapes first (a name inside an address is part of the address), then names, longest first, so
    # "Priya Tan" is one person and not "Priya" plus a stray "Tan".
    for kind, pattern in DETECTORS:
        text = p.replace(kind, pattern, text)
    for name in sorted({n.strip() for n in names if n and len(n.strip()) >= 2}, key=len, reverse=True):
        if name.casefold() in p.keep:
            continue
        pat = re.compile(rf"(?<!\w|\[){re.escape(name)}(?!\w)", re.IGNORECASE)
        text = p.replace("person", pat, text)
    return text


def redact(text: str, *, names: Iterable[str] = (), keep: Iterable[str] = ()) -> Redaction:
    """Redact one text. `names`: people to take out by name. `keep`: the owner's own values to leave."""
    p = _Pass(keep)
    out = _run(p, text, list(names))
    return Redaction(text=out, found=p.found, mapping=p.mapping)


def restore(text: str, mapping: dict[str, str]) -> str:
    """Put the originals back into a reply that used the placeholders."""
    for placeholder, original in mapping.items():
        text = text.replace(placeholder, original)
    return text


def redact_messages(
    messages: list[dict[str, Any]], *, names: Iterable[str] = (), keep: Iterable[str] = ()
) -> tuple[list[dict[str, Any]], Redaction]:
    """Redact the text in a model conversation (plain strings, text blocks and tool results) without
    changing the original list. One pass over the whole conversation, so a value keeps one placeholder."""
    p = _Pass(keep)
    name_list = list(names)

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return _run(p, node, name_list)
        if isinstance(node, list):
            return [walk(x) for x in node]
        if isinstance(node, dict):
            return {k: (walk(v) if k in ("text", "content") else v) for k, v in node.items()}
        return node

    out = [walk(m) for m in messages]
    return out, Redaction(text="", found=p.found, mapping=p.mapping)


def restore_any(value: Any, mapping: dict[str, str]) -> Any:
    """`restore` through nested replies: strings, lists, dicts and pydantic models."""
    if not mapping:
        return value
    if isinstance(value, str):
        return restore(value, mapping)
    if isinstance(value, list):
        return [restore_any(x, mapping) for x in value]
    if isinstance(value, dict):
        return {k: restore_any(v, mapping) for k, v in value.items()}
    dump = getattr(value, "model_dump", None)
    if dump is not None:
        return type(value).model_validate(restore_any(dump(), mapping))
    return value
