"""The passport file format (schema version 1) and the validation every import goes through.

A passport is a zip with plain files (docs/memory-passport.md). Nothing in it is trusted: the
parser only reads the named members (it never extracts to disk, so file names inside the zip
cannot escape anywhere), caps every size, and checks every record. What an imported fact may be
seen by is never read from the file; the importer decides that.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

FORMAT = "superaide-memory-passport"
SCHEMA_VERSION = 1

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_MEMBER_BYTES = 20 * 1024 * 1024  # one file inside the zip, uncompressed (a zip bomb stops here)
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_FACTS = 5000
MAX_SOURCES = 200
MAX_PEOPLE = 500
MAX_FACT_CHARS = 2000
MAX_SOURCE_CHARS = 500_000
MAX_ERRORS_SHOWN = 10

CATEGORIES = ("about", "work", "person", "preference", "urgency", "availability", "sharing", "other")
SOURCE_KINDS = ("pdf", "docx", "text", "web", "github")
IMPORTED_CONFIDENCE_CAP = 0.6  # imported facts rank below anything the owner told Ava directly

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class PassportError(Exception):
    """The file is not a passport we can read at all."""


@dataclass
class FactRecord:
    key: str
    content: str
    category: str
    confidence: float
    valid_from: datetime | None
    source_key: str | None
    origin: str


@dataclass
class SourceRecord:
    key: str
    kind: str
    title: str
    source: str
    text: str
    warnings: list[str]


@dataclass
class PersonRecord:
    name: str
    relationship: str
    notes: str


@dataclass
class Passport:
    assistant: str
    schema_version: int
    facts: list[FactRecord] = field(default_factory=list)
    sources: list[SourceRecord] = field(default_factory=list)
    people: list[PersonRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # why records were left out (first few)
    skipped_count: int = 0

    def skip(self, why: str) -> None:
        self.skipped_count += 1
        if len(self.skipped) < MAX_ERRORS_SHOWN:
            self.skipped.append(why)


def clean_text(value: Any, limit: int) -> str:
    """One line of plain text: control characters dropped, whitespace collapsed, cut to `limit`."""
    if not isinstance(value, str):
        return ""
    return " ".join(_CONTROL.sub("", value).split())[:limit]


def _body(value: Any, limit: int) -> str:
    """Text that keeps its line breaks (a document's text)."""
    if not isinstance(value, str):
        return ""
    return _CONTROL.sub("", value).replace("\r\n", "\n")[:limit]


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    if when > now:
        return now
    return when if when.year >= 1990 else None


def _lines(raw: bytes, what: str) -> list[tuple[int, Any]]:
    out: list[tuple[int, Any]] = []
    for n, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append((n, json.loads(line)))
        except ValueError:
            out.append((n, ValueError(f"{what} line {n} is not valid JSON")))
    return out


def _read_member(zf: zipfile.ZipFile, name: str, budget: list[int]) -> bytes | None:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.flag_bits & 0x1:
        raise PassportError("That passport is password-protected. Export it again without a password.")
    if info.file_size > MAX_MEMBER_BYTES:
        raise PassportError(f"{name} is too large (the limit is {MAX_MEMBER_BYTES // 1024 // 1024} MB per file).")
    with zf.open(info) as handle:
        data = handle.read(MAX_MEMBER_BYTES + 1)  # trust what we read, not what the header says
    if len(data) > MAX_MEMBER_BYTES:
        raise PassportError(f"{name} is too large (the limit is {MAX_MEMBER_BYTES // 1024 // 1024} MB per file).")
    budget[0] += len(data)
    if budget[0] > MAX_TOTAL_BYTES:
        raise PassportError("That passport is too large once unpacked.")
    return data


def parse(data: bytes) -> Passport:
    """Read and validate a passport. Raises PassportError when the file as a whole is unusable;
    individual bad records are left out and listed in `skipped`."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise PassportError(f"That file is too large (the limit is {MAX_UPLOAD_BYTES // 1024 // 1024} MB).")
    if data[:2] != b"PK":  # not a zip: accept a bare facts.jsonl from another assistant
        return _parse_bare_facts(data)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PassportError("That isn't a readable zip file.") from exc
    with zf:
        budget = [0]
        manifest_raw = _read_member(zf, "manifest.json", budget)
        if manifest_raw is None:
            raise PassportError("That zip has no manifest.json, so it isn't a memory passport.")
        try:
            manifest = json.loads(manifest_raw)
        except ValueError as exc:
            raise PassportError("manifest.json is not valid JSON.") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise PassportError("manifest.json does not say it is a Superaide memory passport.")
        version = manifest.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise PassportError("manifest.json has no usable schema_version.")
        if version > SCHEMA_VERSION:
            raise PassportError(
                f"This passport uses schema version {version}; this Ava reads up to {SCHEMA_VERSION}. Update Ava first."
            )
        passport = Passport(assistant=clean_text(manifest.get("assistant"), 100), schema_version=version)
        _read_sources(passport, _read_member(zf, "sources.jsonl", budget))
        _read_facts(passport, _read_member(zf, "facts.jsonl", budget))
        _read_people(passport, _read_member(zf, "people.jsonl", budget))
    return passport


def _parse_bare_facts(data: bytes) -> Passport:
    if b"\x00" in data[:4096]:
        raise PassportError("That isn't a passport or a facts file (JSON Lines).")
    passport = Passport(assistant="", schema_version=SCHEMA_VERSION)
    _read_facts(passport, data)
    if not passport.facts:
        raise PassportError("I found no facts in that file. A passport is a zip; a bare file must be JSON Lines.")
    return passport


def _read_sources(passport: Passport, raw: bytes | None) -> None:
    for n, rec in _lines(raw or b"", "sources.jsonl"):
        if len(passport.sources) >= MAX_SOURCES:
            passport.skip(f"more than {MAX_SOURCES} sources: the rest were left out")
            break
        if not isinstance(rec, dict):
            passport.skip(f"sources.jsonl line {n} is not an object")
            continue
        key = clean_text(rec.get("id"), 80)
        text = _body(rec.get("text"), MAX_SOURCE_CHARS)
        if not key or not text.strip():
            passport.skip(f"sources.jsonl line {n} needs an id and text")
            continue
        kind = str(rec["kind"]) if rec.get("kind") in SOURCE_KINDS else "text"
        raw_warnings = rec.get("warnings")
        warnings: list[Any] = raw_warnings if isinstance(raw_warnings, list) else []
        passport.sources.append(
            SourceRecord(
                key=key,
                kind=kind,
                title=clean_text(rec.get("title"), 300),
                source=clean_text(rec.get("source"), 1000),
                text=text,
                warnings=[clean_text(w, 300) for w in warnings[:10] if isinstance(w, str)],
            )
        )


def _read_facts(passport: Passport, raw: bytes | None) -> None:
    for n, rec in _lines(raw or b"", "facts.jsonl"):
        if len(passport.facts) >= MAX_FACTS:
            passport.skip(f"more than {MAX_FACTS} facts: the rest were left out")
            break
        if isinstance(rec, ValueError):
            passport.skip(str(rec))
            continue
        if not isinstance(rec, dict):
            passport.skip(f"facts.jsonl line {n} is not an object")
            continue
        content = clean_text(rec.get("content"), MAX_FACT_CHARS + 1)
        if not content or len(content) > MAX_FACT_CHARS:
            passport.skip(f"facts.jsonl line {n} has no content, or more than {MAX_FACT_CHARS} characters")
            continue
        if rec.get("valid_to"):  # a fact that was already untrue is history, not memory
            passport.skip(f"facts.jsonl line {n} is no longer true")
            continue
        category = str(rec["category"]) if rec.get("category") in CATEGORIES else "other"
        conf = rec.get("confidence")
        confidence = float(conf) if isinstance(conf, int | float) and not isinstance(conf, bool) else 1.0
        passport.facts.append(
            FactRecord(
                key=clean_text(rec.get("id"), 80),
                content=content,
                category=category,
                confidence=max(0.0, min(IMPORTED_CONFIDENCE_CAP, confidence)),
                valid_from=parse_time(rec.get("valid_from")),
                source_key=clean_text(rec.get("source_id"), 80) or None,
                origin=clean_text(rec.get("origin"), 30),
            )
        )


def _read_people(passport: Passport, raw: bytes | None) -> None:
    for n, rec in _lines(raw or b"", "people.jsonl"):
        if len(passport.people) >= MAX_PEOPLE:
            passport.skip(f"more than {MAX_PEOPLE} people: the rest were left out")
            break
        if not isinstance(rec, dict):
            passport.skip(f"people.jsonl line {n} is not an object")
            continue
        name = clean_text(rec.get("name"), 120)
        if not name:
            passport.skip(f"people.jsonl line {n} has no name")
            continue
        passport.people.append(
            PersonRecord(
                name=name,
                relationship=clean_text(rec.get("relationship"), 30).lower() or "other",
                notes=clean_text(rec.get("notes"), 1000),
            )
        )
