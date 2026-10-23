"""The rules that turn skill receipts into a trust tier, and the hash chain that protects them (issue #131).

THIS FILE USES ONLY THE PYTHON STANDARD LIBRARY, on purpose. The server imports it, and so does
`backend/scripts/skill_tier.py`, which anyone can run on an exported receipt log to recompute every tier and check
the chain without installing anything. A tier is a computed fact: there is no other place it is decided.

A receipt is a dict with: seq, prev_hash, entry_hash, skill_id, version, effect_class, outcome, owner_ref,
ref_seq, latency_s, batch, at. It holds no message text, no names and no arguments.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

GENESIS = "0" * 64
OUTCOMES = ("approved_unedited", "edited", "rejected", "reverted")
HASHED_FIELDS = (
    "seq", "skill_id", "version", "effect_class", "outcome", "owner_ref", "ref_seq", "latency_s", "batch", "at",
)  # fmt: skip


@dataclass(frozen=True)
class Rules:
    min_days: int = 3  # an owner counts only after this many days between their first and last decision on a skill
    min_owner_receipts: int = 3
    owner_cap: int = 20  # at most this many of one owner's receipts count per skill version
    observed_receipts: int = 20
    observed_owners: int = 3
    trusted_receipts: int = 100
    trusted_owners: int = 5
    trusted_unedited_rate: float = 0.8
    trusted_max_revert_rate: float = 0.02
    demote_revert_rate: float = 0.10  # at or above `observed_receipts`, a revert rate over this demotes


def fields_of(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row.get(k) for k in HASHED_FIELDS}


def entry_digest(prev_hash: str, fields: dict[str, Any]) -> str:
    body = json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def verify_chain(rows: list[dict[str, Any]]) -> tuple[bool, int | None, str]:
    """(ok, seq of the first bad receipt, why). Rows must be in seq order starting at 1."""
    prev = GENESIS
    for expected, row in enumerate(rows, start=1):
        if row.get("seq") != expected:
            return False, row.get("seq"), "a receipt is missing or out of order"
        if row.get("prev_hash") != prev:
            return False, expected, "the link to the previous receipt is wrong"
        if row.get("entry_hash") != entry_digest(prev, fields_of(row)):
            return False, expected, "a receipt was changed"
        prev = row["entry_hash"]
    return True, None, ""


def merkle_root(hashes: list[str]) -> str:
    if not hashes:
        return GENESIS
    level = list(hashes)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [hashlib.sha256((a + b).encode()).hexdigest() for a, b in zip(level[::2], level[1::2])]  # noqa: B905 (3.9-safe)
    return level[0]


def daily_roots(rows: list[dict[str, Any]]) -> dict[str, str]:
    """One Merkle root per UTC day (YYYY-MM-DD), over that day's entry hashes: publish it, check it later."""
    days: dict[str, list[str]] = {}
    for r in rows:
        days.setdefault(str(r["at"])[:10], []).append(r["entry_hash"])
    return {d: merkle_root(h) for d, h in sorted(days.items())}


def _day(value: Any) -> float:
    return datetime.fromisoformat(str(value)).timestamp() / 86400


def compute_tier(rows: list[dict[str, Any]], rules: Rules | None = None) -> dict[str, Any]:
    """The tier of ONE skill version from its receipts (all rows must be that version's). Pure and deterministic:
    the same receipts always give the same answer, whoever runs it."""
    rules = rules or Rules()
    by_owner: dict[str, list[dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: (str(r["at"]), r.get("seq", 0))):
        by_owner.setdefault(r["owner_ref"], []).append(r)
    counted: list[dict[str, Any]] = []
    owners = 0
    for rs in by_owner.values():
        span = _day(rs[-1]["at"]) - _day(rs[0]["at"])
        if len(rs) < rules.min_owner_receipts or span < rules.min_days:
            continue  # too new or too thin to count: nagging a skill for a day proves nothing
        owners += 1
        counted += rs[: rules.owner_cap]
    n = {o: sum(1 for r in counted if r["outcome"] == o) for o in OUTCOMES}
    decided = n["approved_unedited"] + n["edited"]
    total = len(counted)
    unedited = n["approved_unedited"] / decided if decided else 0.0
    revert = n["reverted"] / decided if decided else 0.0
    if total >= rules.observed_receipts and revert >= rules.demote_revert_rate:
        tier = "demoted"
    elif (
        total >= rules.trusted_receipts and owners >= rules.trusted_owners
        and unedited >= rules.trusted_unedited_rate and revert <= rules.trusted_max_revert_rate
    ):  # fmt: skip
        tier = "trusted"
    elif total >= rules.observed_receipts and owners >= rules.observed_owners:
        tier = "observed"
    else:
        tier = "new"
    return {
        "tier": tier, "counted": total, "qualified_owners": owners, "outcomes": n,
        "unedited_rate": round(unedited, 4), "revert_rate": round(revert, 4), "rules": asdict(rules),
    }  # fmt: skip


def compute_all(rows: list[dict[str, Any]], rules: Rules | None = None) -> list[dict[str, Any]]:
    rules = rules or Rules()
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r["skill_id"], r["version"]), []).append(r)
    return [
        {"skill_id": s, "version": v, "effect_class": rs[-1]["effect_class"], **compute_tier(rs, rules)}
        for (s, v), rs in sorted(groups.items())
    ]
