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

Round trip: a value always comes back exactly as it was written (the placeholder is per exact
spelling, so "Will" and "will" are two placeholders), restoring is one pass that tolerates the case,
spacing and markdown a model may add, and `StreamRestorer` does the same for text that streams in pieces.

What this does not do: read faces or text in images, find a name nobody listed (except a display name
in a mail header such as "From: Priya Tan <..>"), or prove where the pass ran. Those need the on-device
model and a device attestation, which are not built.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
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

# Ids by country shape. Digits written in other scripts count: \d is Unicode-aware.
AADHAAR = re.compile(r"(?<![\w.])[2-9]\d{3}\s\d{4}\s\d{4}(?![\w])|(?<!\d)[2-9]\d{11}(?!\d)")
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
DNI = re.compile(r"\b\d{8}[- ]?[A-HJ-NP-TV-Z]\b|\b[XYZ][- ]?\d{7}[- ]?[A-Z]\b")
NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b")
NINO = re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b")
# "passport A1234567", "DNI: 12345678Z", "Aadhaar no 1234 5678 9012": the value after the word is the id.
ID_WORD = re.compile(
    r"(?i:\b(?:passport|pasaporte|dni|nie|nric|aadhaar|aadhar|pan|ssn|licen[cs]e|id)\b)"
    r"\s*(?i:no\.?|number|num\.?|#|n[ºo°]\.?)?\s*[:#]?\s*([A-Z0-9][A-Z0-9 -]{4,18}[A-Z0-9])"
)
# Phones that are bare digit runs in their own country's shape (India 10 digits from 6-9, Spain 9 from 6/7).
IN_MOBILE = re.compile(r"(?<![\w.])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\w)")
ES_MOBILE = re.compile(r"(?<![\w.])(?:\+?34[\s-]?)?[67]\d{2}[\s.-]?\d{3}[\s.-]?\d{3}(?!\w)")
# Addresses: a labelled one (to the end of the line), a street shape in English or Spanish, Hindi address words,
# and postal codes written with their country or state.
ADDRESS_LABEL = re.compile(
    r"(?im)(?:\b(?:home |billing |mailing |postal |delivery |shipping |street |work )?address|\badres|"
    r"\bdirecci[óo]n(?: postal)?|\bdomicilio|ship(?:ping)? to|पता|पते)\s*[:：\-–]\s*([^\n]{6,140})"
)
STREET_EN = re.compile(
    r"\b\d{1,5}[A-Za-z]?(?:-\d{1,4})?\s+(?:[A-Z][\w'.-]*\s+){0,3}[A-Z][\w'.-]*\s+"
    r"(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd|Way|Court|Ct|Place|Pl|Crescent|Close|"
    r"Terrace|Walk|Park|Gardens|Square)\b\.?"
    r"(?:\s*,?\s*(?:#\d{1,3}-\d{1,4}|(?:Apt|Apartment|Unit|Flat|Floor|Suite|Ste)\.?\s*[\w-]+))?"
    r"(?:\s*,\s*[A-Z][\w ]{2,25}?(?=,|\s+\d))?"
    r"(?:,?\s*(?:Singapore|[A-Z]{2})?\s*\d{5,6}(?:-\d{4})?\b)?"
)
# Singapore and Malaysian shapes: "7 Jalan Besar", "Blk 123 Ang Mo Kio Ave 3 #04-56".
STREET_SG = re.compile(
    r"\b(?:\d{1,5}[A-Za-z]?\s+(?:Jalan|Jln|Lorong|Lor|Bukit|Taman|Persiaran)\s+[A-Z][\w']*(?:\s+[A-Z][\w']*){0,2}|"
    r"(?:Blk|Block)\.?\s*\d{1,4}[A-Za-z]?\s+(?:[A-Z][\w']*\s+){1,3}(?:Ave|Avenue|Street|St|Road|Rd|Drive|Dr|Central)"
    r"(?:\s+\d{1,2})?)(?:\s*,?\s*#\d{1,3}-\d{1,4})?"
)
STREET_ES = re.compile(
    r"\b(?:Calle|C/|Avenida|Avda\.?|Av\.?|Plaza|Paseo|Camino|Ronda|Carrer|Traves[ií]a)\s+"
    r"(?:de(?:l|\s+la|\s+los|\s+las)?\s+)?[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ'.-]*(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][\wÁÉÍÓÚÑáéíóúñ'.-]*){0,3}"
    r"\s*,?\s*(?:n[º°o.]\s*)?\d{1,4}(?:\s*,?\s*\d{1,2}\s*[º°]?\s*[A-Za-z]?)?"
    r"(?:\s*,?\s*\d{5}(?:\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)?)?"
)
_HI = r"(?:मकान|फ्लैट|गली|सड़क|मार्ग|सेक्टर|नगर|कॉलोनी|मोहल्ला|पिन)"
ADDRESS_HI = re.compile(rf"(?:{_HI}[^\n,।]{{0,30}}[,\s]*){{1,6}}")
POSTAL = re.compile(
    r"\b(?:Singapore|S)\s?\d{6}\b|\b[A-Z]{2}\s\d{5}(?:-\d{4})?\b|\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b|"
    r"(?i:\b(?:PIN|pincode|पिन))[\s:.-]*\d{6}\b"
)
# "From: Priya Tan <priya@x.com>": the display name in a mail header (or a quoted one) is a person.
HEADER_LINE = re.compile(r"(?im)^[>\s]*(?:from|to|cc|bcc|de|para|sent by|enviado por|प्रेषक|प्रति)\s*:\s*(.+)$")
HEADER_NAME = re.compile(r'(?:^|[,;])\s*"?([^<>"@,;\n:]{2,60}?)"?\s*<')

# kind, pattern, group (0 = the whole match), whether the digit-run sanity checks apply.
# Order matters: the narrower shapes win over the broader digit runs.
DETECTORS: tuple[tuple[str, re.Pattern[str], int, bool], ...] = (
    ("email", EMAIL, 0, True),
    ("iban", IBAN, 0, True),
    ("card", CARD, 0, True),  # before the id shapes: a card number is also four groups of four
    ("address", ADDRESS_LABEL, 1, False),
    ("address", STREET_EN, 0, False),
    ("address", STREET_SG, 0, False),
    ("address", STREET_ES, 0, False),
    ("address", ADDRESS_HI, 0, False),
    ("national id", ID_WORD, 1, False),
    ("national id", NATIONAL_ID, 0, True),
    ("national id", PAN, 0, False),
    ("national id", DNI, 0, False),
    ("national id", NRIC, 0, False),
    ("national id", NINO, 0, False),
    ("national id", AADHAAR, 0, False),
    ("ip address", IPV4, 0, True),
    ("phone", IN_MOBILE, 0, False),
    ("phone", ES_MOBILE, 0, False),
    ("phone", PHONE, 0, True),
    ("address", POSTAL, 0, False),
    ("handle", HANDLE, 0, True),
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


def _fold(text: str) -> str:
    """The text with accents removed, character for character, so a match found in it points at the same place in
    the original ("Jose" finds "José"). Letters of other scripts are left as they are."""
    out = []
    for ch in text:
        d = unicodedata.normalize("NFD", ch)
        out.append(d[0] if len(d) > 1 and d[0].isascii() and all(unicodedata.combining(c) for c in d[1:]) else ch)
    return "".join(out)


_SEP = r"(?:[\s._+\-]|%20)+"  # what may sit between the parts of a name: a space, a dot, a hyphen, %20 in a URL


def _name_pattern(name: str) -> re.Pattern[str] | None:
    parts = [re.escape(_fold(t)) for t in re.split(r"[\s._+\-]+", name.strip()) if t]
    if not parts or sum(len(x) for x in parts) < 2:
        return None
    return re.compile(rf"(?<!\w|\[){_SEP.join(parts)}(?!\w)", re.IGNORECASE)


class _Pass:
    def __init__(self, keep: Iterable[str]) -> None:
        self.keep = {k.casefold() for k in keep if k}
        self.numbers: dict[tuple[str, str], str] = {}
        self.per_kind: dict[str, int] = {}
        self.found: dict[str, int] = {}
        self.mapping: dict[str, str] = {}

    def placeholder(self, kind: str, value: str) -> str:
        # The exact spelling is the key (not its lower case), so what comes back is what went out.
        key = (kind, value)
        if key not in self.numbers:
            self.per_kind[kind] = self.per_kind.get(kind, 0) + 1
            self.numbers[key] = f"[{kind} {self.per_kind[kind]}]"
            self.mapping[self.numbers[key]] = value
        self.found[kind] = self.found.get(kind, 0) + 1
        return self.numbers[key]

    def _kept(self, value: str) -> bool:
        return value.casefold() in self.keep or value.strip().casefold() in self.keep

    def replace(self, kind: str, pattern: re.Pattern[str], text: str, group: int = 0, checks: bool = True) -> str:
        def sub(m: re.Match[str]) -> str:
            whole = m.group(0)
            value = m.group(group) if group else whole
            if value is None or not value.strip():
                return whole
            if self._kept(value):
                return whole
            digits = re.sub(r"\D", "", value)
            if checks and kind == "card" and not (13 <= len(digits) <= 19 and luhn(digits)):
                return whole
            if (
                checks
                and kind == "phone"
                and (
                    DATE_SHAPE.match(value.strip())
                    or not 8 <= len(digits) <= 15
                    or value.strip().isdigit()  # a bare run of digits is an order number or an id, not a phone
                )
            ):
                return whole
            if group:
                at = m.start(group) - m.start(0)
                lead = value[: len(value) - len(value.lstrip())]
                core = value.strip()
                return whole[: at + len(lead)] + self.placeholder(kind, core) + whole[at + len(lead) + len(core) :]
            return self.placeholder(kind, value.strip())

        return pattern.sub(sub, text)

    def replace_names(self, pattern: re.Pattern[str], text: str) -> str:
        """Names are found in the accent-folded text and replaced in the original at the same places."""
        folded = _fold(text)
        out, last = [], 0
        for m in pattern.finditer(folded):
            value = text[m.start() : m.end()]
            if self._kept(value):
                continue
            out.append(text[last : m.start()])
            out.append(self.placeholder("person", value))
            last = m.end()
        out.append(text[last:])
        return "".join(out)

    def header_names(self, text: str) -> str:
        """The display names in mail header lines ("From: Priya Tan <..>"), even when nobody listed them."""

        def line(m: re.Match[str]) -> str:
            body = m.group(1)

            def one(n: re.Match[str]) -> str:
                name = n.group(1).strip()
                if not name or self._kept(name) or re.fullmatch(r"\[[a-z ]+ \d+\]", name):
                    return n.group(0)
                at = n.start(1) - n.start(0)
                return n.group(0)[:at] + self.placeholder("person", name) + n.group(0)[at + len(n.group(1)) :]

            return m.group(0)[: m.start(1) - m.start(0)] + HEADER_NAME.sub(one, body)

        return HEADER_LINE.sub(line, text)


def name_variants(names: Iterable[str]) -> list[str]:
    """The names to take out: each full name and each of its parts of three letters or more ("Priya Tan",
    "Priya", "Tan"), so a first name alone is caught too. Common words that are also names are taken out too:
    over-redacting is the safe error, and the reply gets the original back."""
    out: set[str] = set()
    for n in names:
        n = " ".join(str(n or "").split())
        if len(n) < 2:
            continue
        out.add(n)
        for part in re.split(r"[\s.\-]+", n):
            if len(part) >= 3:
                out.add(part)
    return sorted(out)


_ZW = "\u200b"
_LITERAL = re.compile(r"\[(?=\s*[A-Za-z][A-Za-z ]{1,16}?\s+\d{1,4}\s*\])")


def _run(p: _Pass, text: str, names: list[str]) -> str:
    text = _LITERAL.sub("[" + _ZW, text)  # text that already looks like a placeholder: marked, restored as written
    # Shapes first (a name inside an address is part of the address), then names, longest first, so
    # "Priya Tan" is one person and not "Priya" plus a stray "Tan".
    for kind, pattern, group, checks in DETECTORS:
        text = p.replace(kind, pattern, text, group, checks)
    text = p.header_names(text)
    patterns = []
    for name in sorted({n.strip() for n in names if n and len(n.strip()) >= 2}, key=len, reverse=True):
        if name.casefold() in p.keep:
            continue
        if (pat := _name_pattern(name)) is not None:
            patterns.append(pat)
    for pat in patterns:
        text = p.replace_names(pat, text)
    return text


def redact(text: str, *, names: Iterable[str] = (), keep: Iterable[str] = ()) -> Redaction:
    """Redact one text. `names`: people to take out by name. `keep`: the owner's own values to leave."""
    p = _Pass(keep)
    out = _run(p, text, list(names))
    return Redaction(text=out, found=p.found, mapping=p.mapping)


_PLACEHOLDER = re.compile(r"\\?\[\s*([A-Za-z][A-Za-z ]{1,16}?)\s+(\d{1,4})\s*\\?\]")


def _key(kind: str, number: str) -> str:
    return f"[{' '.join(kind.lower().split())} {int(number)}]"


def restore(text: str, mapping: dict[str, str]) -> str:
    """Put the originals back into a reply that used the placeholders. One pass, so an original that itself
    looks like a placeholder is never expanded again; tolerant of the case, spacing and escaped brackets a
    model may write ("[Email 1]", "[ email  1 ]", "\\[email 1\\]")."""
    if not mapping:
        return text
    folded = {k.casefold(): v for k, v in mapping.items()}

    def sub(m: re.Match[str]) -> str:
        return folded.get(_key(m.group(1), m.group(2)), m.group(0))

    return _PLACEHOLDER.sub(sub, text).replace("[" + _ZW, "[")


class StreamRestorer:
    """`restore` for text that arrives in pieces: a placeholder cut in two by a chunk boundary is held back until it
    is whole, and everything else goes through at once. Call `flush` at the end for whatever is still held."""

    MAX_HELD = 28  # longest placeholder we expect: "[national id 1234]" plus the spaces a model may add

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.held = ""

    def feed(self, chunk: str) -> str:
        text = self.held + chunk
        self.held = ""
        cut = text.rfind("[")
        if cut == -1:
            cut = text.rfind("\\[")
        if cut != -1 and "]" not in text[cut:] and len(text) - cut <= self.MAX_HELD:
            text, self.held = text[:cut], text[cut:]
        return restore(text, self.mapping)

    def flush(self) -> str:
        out, self.held = self.held, ""
        return restore(out, self.mapping)


def redact_messages(
    messages: list[dict[str, Any]], *, names: Iterable[str] = (), keep: Iterable[str] = ()
) -> tuple[list[dict[str, Any]], Redaction]:
    """Redact the text in a model conversation (plain strings, text blocks, tool results and the arguments of
    earlier tool calls) without changing the original list. One pass over the whole conversation, so a value
    keeps one placeholder. Thinking blocks are left alone: they are signed and must go back unchanged."""
    p = _Pass(keep)
    name_list = list(names)

    def everything(node: Any) -> Any:  # the arguments of a tool call: every string in them
        if isinstance(node, str):
            return _run(p, node, name_list)
        if isinstance(node, list):
            return [everything(x) for x in node]
        if isinstance(node, dict):
            return {k: everything(v) for k, v in node.items()}
        return node

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return _run(p, node, name_list)
        if isinstance(node, list):
            return [walk(x) for x in node]
        if isinstance(node, dict):
            if node.get("type") in {"thinking", "redacted_thinking"}:
                return node
            return {
                k: (everything(v) if k == "input" else walk(v) if k in ("text", "content") else v)
                for k, v in node.items()
            }
        return node

    out = [walk(m) for m in messages]
    return out, Redaction(text="", found=p.found, mapping=p.mapping)


def restore_any(value: Any, mapping: dict[str, str]) -> Any:
    """`restore` through nested replies: strings, lists, dicts and pydantic models. Signed thinking blocks are
    left exactly as the model wrote them."""
    if not mapping:
        return value
    if isinstance(value, str):
        return restore(value, mapping)
    if isinstance(value, list):
        return [restore_any(x, mapping) for x in value]
    if isinstance(value, dict):
        if value.get("type") in {"thinking", "redacted_thinking"}:
            return value
        return {k: restore_any(v, mapping) for k, v in value.items()}
    dump = getattr(value, "model_dump", None)
    if dump is not None:
        return type(value).model_validate(restore_any(dump(), mapping))
    return value


def restoring_events(
    on_event: Callable[[str, dict[str, Any]], None], mapping: dict[str, str]
) -> tuple[Callable[[str, dict[str, Any]], None], Callable[[], None]]:
    """Wrap a live-progress callback so the text and thinking the owner watches stream by already has its
    originals back. Returns (callback, finish); call `finish` after the model call to release held text."""
    text, thinking = StreamRestorer(mapping), StreamRestorer(mapping)

    def relay(kind: str, data: dict[str, Any]) -> None:
        if kind == "text":
            out = text.feed(str(data.get("delta", "")))
            if out:
                on_event(kind, {**data, "delta": out})
        elif kind == "thinking":
            out = thinking.feed(str(data.get("delta", "")))
            if out:
                on_event(kind, {**data, "delta": out})
        elif kind == "block":
            on_event(kind, restore_any(data, mapping))
        else:
            on_event(kind, data)

    def finish() -> None:
        for kind, r in (("text", text), ("thinking", thinking)):
            if rest := r.flush():
                on_event(kind, {"delta": rest})

    return relay, finish
