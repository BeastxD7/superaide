"""Skill trust signals that are measured, not claimed (issue #130).

Two ideas, both decided in code and neither calling a model:

1. A disagreement ledger. Every skill version is read by several independent scanners. No single scanner can
   promote a skill: the route comes from the PATTERN of their verdicts (all clean, a lone flag, a majority flag,
   all flag). Two small offline scanners ship here; an outside scanner's verdict can be recorded the same way.
2. A behaviour card the runtime writes, from counters it saw across installs (calls, errors, reverts, effect classes
   called, host names contacted). The counters are an allowlist: no arguments, no results, no message text. The card
   is shown only above a minimum number of installs. Any gap between what the skill did and its manifest demotes the
   skill, and a demoted skill is not offered to Ava (`is_demoted`, checked in registry.active).

Off unless SKILL_SIGNALS_ENABLED. Community skills stay untrusted: nothing here raises what a skill may do.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.config import get_settings
from superaide.skills.manifest import Manifest, Skill
from superaide.skills.models import SkillCounter, SkillDemotion, SkillScan

VERDICTS: tuple[str, ...] = ("clean", "suspicious", "malicious")
ALLOWED_KINDS: tuple[str, ...] = ("call", "error", "revert", "effect", "host")  # the whole telemetry allowlist
EFFECTS: tuple[str, ...] = ("read_public", "read_private", "write_own", "write_external", "send", "spend")
MIN_SCANNERS = 2
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")


def enabled() -> bool:
    return get_settings().skill_signals_enabled


# --- scanners -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    verdict: str
    reasons: tuple[str, ...] = ()


class Scanner(Protocol):
    name: str

    def scan(self, skill: Skill) -> Finding: ...


# (code, pattern, is_severe). A severe hit alone is "malicious"; any other hit is "suspicious".
PATTERNS: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    ("pipe_to_shell", re.compile(r"(curl|wget)\s[^\n|]*\|\s*(ba|z)?sh\b", re.I), True),
    (
        "override_instructions",
        re.compile(r"ignore (all |any )?(previous|prior|above) (instructions|rules)", re.I),
        True,
    ),
    ("credential_path", re.compile(r"(~|\$HOME)?/?\.(ssh|aws|gnupg)\b|\.env\b|id_rsa", re.I), False),
    ("hidden_exec", re.compile(r"base64\s+(-d|--decode)|\beval\s*\(|\bexec\s*\(", re.I), False),
    ("secrecy", re.compile(r"(do not|don't|never) (tell|show|reveal to) the (user|owner)", re.I), True),
)


class PatternScanner:
    """Reads the instructions text for known bad shapes."""

    name = "pattern"

    def scan(self, skill: Skill) -> Finding:
        text = f"{skill.description}\n{skill.instructions}"
        hits = [(code, severe) for code, rx, severe in PATTERNS if rx.search(text)]
        if not hits:
            return Finding("clean")
        verdict = "malicious" if any(severe for _, severe in hits) else "suspicious"
        return Finding(verdict, tuple(code for code, _ in hits))


class ManifestScanner:
    """Reads the manifest for reach that the skill's purpose rarely needs."""

    name = "manifest"

    def scan(self, skill: Skill) -> Finding:
        m = skill.manifest
        reasons: list[str] = []
        if "*" in m.network and m.tier == "community":
            reasons.append("open_network")
        if any(re.fullmatch(r"\d+\.\d+\.\d+\.\d+", h) for h in m.network):
            reasons.append("ip_literal_host")
        effects = {t.effect for t in m.tools}
        if m.tier == "community" and effects & {"write_external", "send", "spend"}:
            reasons.append("side_effects_from_community")
        if effects & {"read_private"} and "*" in m.network:
            reasons.append("private_read_plus_open_network")  # can read what Ava knows and reach any host
        if not reasons:
            return Finding("clean")
        severe = "private_read_plus_open_network" in reasons
        return Finding("malicious" if severe else "suspicious", tuple(reasons))


SCANNERS: tuple[Scanner, ...] = (PatternScanner(), ManifestScanner())


def record_scan(
    session: Session, skill_id: str, version: str, scanner: str, finding: Finding, *, now: datetime | None = None
) -> None:
    """Store one scanner's verdict (replacing that scanner's earlier one for this version)."""
    if finding.verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {finding.verdict!r}")
    values = {
        "skill_id": skill_id, "version": version, "scanner": scanner[:40], "verdict": finding.verdict,
        "reasons": list(finding.reasons), "scanned_at": now or datetime.now(UTC),
    }  # fmt: skip
    from superaide.tenancy.context import scope_or_default

    scope = scope_or_default()
    stmt = insert(SkillScan).values(tenant_id=scope.tenant_id, agent_id=scope.agent_id, **values)
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_skill_scan",
            set_={k: stmt.excluded[k] for k in ("verdict", "reasons", "scanned_at")},
        )
    )
    session.flush()


def scan_skill(session: Session, skill: Skill) -> Routing:
    """Run every scanner on this version, store the verdicts, return the route."""
    for scanner in SCANNERS:
        record_scan(session, skill.manifest.id, skill.manifest.version, scanner.name, scanner.scan(skill))
    audit(session, "skills", "skill_scanned", skill_id=skill.manifest.id, version=skill.manifest.version)
    return ledger(session, skill.manifest.id, skill.manifest.version)


# --- the disagreement ledger --------------------------------------------------------------------------


@dataclass(frozen=True)
class Routing:
    pattern: str  # incomplete | unanimous_clean | unanimous_flag | lone_flag | majority_flag
    route: str  # wait | clear | review | block
    may_promote: bool  # only unanimous_clean
    verdicts: dict[str, str] = field(default_factory=dict)


def route_verdicts(verdicts: dict[str, str]) -> Routing:
    """The route comes from the pattern, never from one scanner. A lone flag is the common real case (most flagged
    skills are caught by exactly one scanner), so it goes to a human instead of being passed or dropped."""
    if len(verdicts) < MIN_SCANNERS:
        return Routing("incomplete", "wait", False, dict(verdicts))
    flagged = [s for s, v in verdicts.items() if v != "clean"]
    if not flagged:
        return Routing("unanimous_clean", "clear", True, dict(verdicts))
    if len(flagged) == len(verdicts):
        severe = any(v == "malicious" for v in verdicts.values())
        return Routing("unanimous_flag", "block" if severe else "review", False, dict(verdicts))
    if len(flagged) * 2 > len(verdicts):
        return Routing("majority_flag", "review", False, dict(verdicts))
    return Routing("lone_flag", "review", False, dict(verdicts))


def ledger(session: Session, skill_id: str, version: str) -> Routing:
    rows = session.scalars(select(SkillScan).where(SkillScan.skill_id == skill_id, SkillScan.version == version))
    return route_verdicts({r.scanner: r.verdict for r in rows})


def tier_ceiling(session: Session, skill_id: str, version: str) -> str:
    """The highest tier a skill version may reach: `reviewable` only on a unanimous clean, `blocked` on a
    unanimous severe flag, otherwise `none` (it waits, or a human decides). Scanners can never raise it further."""
    r = ledger(session, skill_id, version)
    return "reviewable" if r.may_promote else "blocked" if r.route == "block" else "none"


def label_concern(session: Session, skill_id: str, version: str, scanner: str, concern: bool) -> None:
    row = session.scalar(
        select(SkillScan).where(
            SkillScan.skill_id == skill_id, SkillScan.version == version, SkillScan.scanner == scanner
        )
    )
    if row is not None:
        row.human_concern = concern
        session.flush()


def disagreement_report(session: Session) -> dict[str, object]:
    """The smallest experiment: among hand-labelled versions, does disagreement predict human concern?
    If the two rates are alike, the ledger is not worth keeping."""
    by_version: dict[tuple[str, str], list[SkillScan]] = {}
    for row in session.scalars(select(SkillScan)):
        by_version.setdefault((row.skill_id, row.version), []).append(row)
    agree, split = [0, 0], [0, 0]  # [labelled, concerning]
    for rows in by_version.values():
        labels = [r.human_concern for r in rows if r.human_concern is not None]
        if not labels:
            continue
        bucket = split if len({r.verdict == "clean" for r in rows}) > 1 else agree
        bucket[0] += 1
        bucket[1] += int(any(labels))
    rate = lambda b: round(b[1] / b[0], 3) if b[0] else None  # noqa: E731
    return {
        "labelled_unanimous": agree[0], "concern_rate_unanimous": rate(agree),
        "labelled_split": split[0], "concern_rate_split": rate(split),
    }  # fmt: skip


# --- telemetry counters and the behaviour card --------------------------------------------------------


def host_of(url_or_host: str | None) -> str | None:
    """A bare lowercase host name, or None. A path, query, port, user or anything else is dropped here."""
    if not url_or_host:
        return None
    raw = url_or_host.strip()
    host = (urlsplit(raw if "//" in raw else f"//{raw}").hostname or "").lower()
    return host if HOST_RE.match(host) and len(host) <= 120 else None


def _bump(session: Session, skill_id: str, version: str, kind: str, key: str) -> None:
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"{kind!r} is not in the telemetry allowlist")
    from superaide.tenancy.context import scope_or_default

    scope = scope_or_default()
    stmt = insert(SkillCounter).values(
        tenant_id=scope.tenant_id, agent_id=scope.agent_id, skill_id=skill_id, version=version, kind=kind,
        key=key, count=1,
    )  # fmt: skip
    session.execute(stmt.on_conflict_do_update(constraint="uq_skill_counter", set_={"count": SkillCounter.count + 1}))


def observe(session: Session, skill: Skill, tool: str, *, is_error: bool = False, url: str | None = None) -> list[str]:
    """Count one call of one of this skill's tools. Returns the gaps found (empty when it matched the manifest)
    and demotes the skill version if there are any. Only names and classes are kept."""
    if not enabled():
        return []
    m = skill.manifest
    declared = next((t for t in m.tools if t.name == tool), None)
    effect = declared.effect if declared else "undeclared"
    _bump(session, m.id, m.version, "call", "total")
    if is_error:
        _bump(session, m.id, m.version, "error", "total")
    if effect in EFFECTS:
        _bump(session, m.id, m.version, "effect", effect)
    gaps: list[str] = [] if declared else [f"tool:{tool[:60]}"]
    host = host_of(url)
    if host:
        _bump(session, m.id, m.version, "host", host)
        if not host_allowed(m, host):
            gaps.append(f"host:{host}")
    if gaps:
        demote(session, m.id, m.version, gaps)
    return gaps


def observe_revert(session: Session, skill_id: str, version: str) -> None:
    if enabled():
        _bump(session, skill_id, version, "revert", "total")


def host_allowed(manifest: Manifest, host: str) -> bool:
    for allowed in manifest.network:
        a = allowed.lower()
        if a == "*" or host == a or (a.startswith("*.") and host.endswith(a[1:])):
            return True
    return False


@dataclass(frozen=True)
class Card:
    skill_id: str
    version: str
    installs: int
    calls: int
    error_rate: float
    revert_rate: float
    effects: dict[str, int]
    hosts: dict[str, int]


def build_card(
    skill_id: str, version: str, rows: Iterable[tuple[str, str, str, int]], *, min_cohort: int
) -> Card | None:
    """From (owner key, kind, key, count) rows. None while fewer than `min_cohort` distinct owners contributed, so a
    card never describes one person's use."""
    owners: set[str] = set()
    calls = errors = reverts = 0
    effects: dict[str, int] = {}
    hosts: dict[str, int] = {}
    for owner, kind, key, count in rows:
        owners.add(owner)
        if kind == "call":
            calls += count
        elif kind == "error":
            errors += count
        elif kind == "revert":
            reverts += count
        elif kind == "effect":
            effects[key] = effects.get(key, 0) + count
        elif kind == "host":
            hosts[key] = hosts.get(key, 0) + count
    if len(owners) < min_cohort:
        return None
    return Card(
        skill_id, version, len(owners), calls,
        round(errors / calls, 4) if calls else 0.0, round(reverts / calls, 4) if calls else 0.0, effects, hosts,
    )  # fmt: skip


def card(session: Session, skill_id: str, version: str) -> Card | None:
    """The generated card for a version, across the installs this session can see (the hosted store runs this with
    a view over every install; one owner's session sees only their own, so it stays below the floor)."""
    rows = session.scalars(
        select(SkillCounter).where(SkillCounter.skill_id == skill_id, SkillCounter.version == version)
    )
    return build_card(
        skill_id, version, [(str(r.agent_id), r.kind, r.key, r.count) for r in rows],
        min_cohort=get_settings().skill_card_min_cohort,
    )  # fmt: skip


def card_gaps(manifest: Manifest, c: Card) -> list[str]:
    """What the card says the skill did that its manifest does not allow."""
    declared = {t.effect for t in manifest.tools}
    gaps = [f"effect:{e}" for e in sorted(c.effects) if e not in declared]
    gaps += [f"host:{h}" for h in sorted(c.hosts) if not host_allowed(manifest, h)]
    return gaps


# --- demotion and the runtime check -------------------------------------------------------------------


def demote(session: Session, skill_id: str, version: str, gaps: list[str]) -> None:
    row = session.scalar(
        select(SkillDemotion).where(SkillDemotion.skill_id == skill_id, SkillDemotion.version == version)
    )
    if row is None:
        session.add(SkillDemotion(skill_id=skill_id, version=version, gaps=gaps[:10], demoted_at=datetime.now(UTC)))
        session.flush()
        audit(session, "skills", "skill_demoted", skill_id=skill_id, version=version, gaps=gaps[:10])
    elif row.cleared_at is not None:  # a cleared demotion comes back if it happens again
        row.cleared_at, row.gaps, row.demoted_at = None, gaps[:10], datetime.now(UTC)
        session.flush()


def clear_demotion(session: Session, skill_id: str, version: str) -> bool:
    row = session.scalar(
        select(SkillDemotion).where(SkillDemotion.skill_id == skill_id, SkillDemotion.version == version)
    )
    if row is None or row.cleared_at is not None:
        return False
    row.cleared_at = datetime.now(UTC)
    session.flush()
    audit(session, "skills", "skill_demotion_cleared", skill_id=skill_id, version=version)
    return True


def is_demoted(session: Session, skill_id: str, version: str) -> bool:
    """The runtime check: True while this version is demoted. Always False while the flag is off."""
    if not enabled():
        return False
    return (
        session.scalar(
            select(SkillDemotion.id).where(
                SkillDemotion.skill_id == skill_id,
                SkillDemotion.version == version,
                SkillDemotion.cleared_at.is_(None),
            )
        )
        is not None
    )


def view(session: Session) -> dict[str, object]:
    pairs = sorted({(r.skill_id, r.version) for r in session.scalars(select(SkillScan))})
    cards = []
    for skill_id, version in {(c.skill_id, c.version) for c in session.scalars(select(SkillCounter))}:
        c = card(session, skill_id, version)
        cards.append({"skill_id": skill_id, "version": version, "shown": c is not None,
                      **({"card": c.__dict__} if c else {})})  # fmt: skip
    return {
        "ledger": [
            {"skill_id": s, "version": v, **{k: getattr(r, k) for k in ("pattern", "route", "may_promote", "verdicts")}}
            for s, v in pairs
            for r in [ledger(session, s, v)]
        ],
        "cards": cards,
        "demoted": [
            {"skill_id": d.skill_id, "version": d.version, "gaps": d.gaps, "demoted_at": d.demoted_at.isoformat()}
            for d in session.scalars(select(SkillDemotion).where(SkillDemotion.cleared_at.is_(None)))
        ],
        "experiment": disagreement_report(session),
    }
