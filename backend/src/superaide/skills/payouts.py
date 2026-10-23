"""Creator payout LEDGER (issue #132). Design for a future marketplace; it moves no money.

What it does, all in code and in whole minor units (cents, never a float):

- A creator earns a fixed amount for each outcome the owner accepted unedited (a `approved_unedited` receipt),
  minus a platform fee in basis points. Official skills earn nothing: they are ours.
- Only real approvals count. The decision must have taken at least the floor of seconds (a tap that fast was not a
  read), must not be part of a batch, and an owner's approvals for one skill count only up to a daily cap, so an
  auto-approve loop or a skill that nags for approvals earns nothing extra. `detect_abuse` names those owners.
- An accrual is not payable until the clawback window has passed. If the owner reverts the action inside the window,
  a clawback line takes the money back.
- A creator of a `send` or `spend` skill earns only while a refund bond is held. The bond is a balance in this ledger;
  a refund to a harmed owner is drawn from it (a `bond_refund` line), never from other creators.
- Paying is the job of a SettlementProvider. Only the `null` provider exists: it records that nothing was paid.
  Choosing and connecting a real one is the owner's decision, so there is no payment code, no key and no network here.

Off unless PAYOUTS_ENABLED.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.config import get_settings
from superaide.skills import receipts
from superaide.skills.models import PayoutEntry

BOND_EFFECTS = ("send", "spend")


class PayoutError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def enabled() -> bool:
    return get_settings().payouts_enabled


@dataclass(frozen=True)
class PayoutRules:
    per_outcome_minor: int = 50
    fee_bps: int = 2000
    floor_seconds: int = 10
    clawback_days: int = 7
    bond_minor: int = 5000
    daily_owner_cap: int = 10
    currency: str = "USD"


def rules() -> PayoutRules:
    s = get_settings()
    return PayoutRules(
        s.payout_per_outcome_minor, s.payout_fee_bps, s.payout_floor_seconds, s.payout_clawback_days,
        s.payout_bond_minor, s.payout_daily_owner_cap, s.payout_currency.upper(),
    )  # fmt: skip


def split(gross_minor: int, fee_bps: int) -> tuple[int, int]:
    """(creator's share, platform fee) in whole minor units. The fee rounds down; the creator keeps the remainder."""
    fee = gross_minor * fee_bps // 10_000
    return gross_minor - fee, fee


# --- who counts -----------------------------------------------------------------------------------------


def qualify(rows: list[dict[str, Any]], r: PayoutRules) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """(receipts that earn a payout, how many were refused and why). Pure: the same log gives the same answer."""
    earned: list[dict[str, Any]] = []
    refused: dict[str, int] = defaultdict(int)
    per_day: dict[tuple[str, str, str], int] = defaultdict(int)  # (skill, owner, day) -> counted so far
    for row in sorted(rows, key=lambda x: x["seq"]):
        if row["outcome"] != "approved_unedited":
            continue
        if row["latency_s"] < r.floor_seconds:
            refused["under_floor"] += 1
        elif row["batch"]:
            refused["batch_approved"] += 1
        else:
            key = (row["skill_id"], row["owner_ref"], str(row["at"])[:10])
            if per_day[key] >= r.daily_owner_cap:
                refused["over_daily_cap"] += 1
            else:
                per_day[key] += 1
                earned.append(row)
    return earned, dict(refused)


def detect_abuse(rows: list[dict[str, Any]], r: PayoutRules) -> list[dict[str, Any]]:
    """Owners whose pattern looks like an auto-approve loop or a nagging skill. A flag is a question for a person, not
    a verdict; the payout rules above already keep both from earning."""
    out: list[dict[str, Any]] = []
    by_owner: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["outcome"] in ("approved_unedited", "edited"):
            by_owner[(row["skill_id"], row["owner_ref"])].append(row)
    for (skill, owner), rs in sorted(by_owner.items()):
        fast = sum(1 for x in rs if x["latency_s"] < r.floor_seconds)
        batched = sum(1 for x in rs if x["batch"])
        days: dict[str, int] = defaultdict(int)
        for x in rs:
            days[str(x["at"])[:10]] += 1
        worst = max(days.values())
        if fast >= 5 and fast * 2 >= len(rs):
            out.append(
                {"skill_id": skill, "owner_ref": owner, "flag": "auto_approve_loop", "fast": fast, "of": len(rs)}
            )
        if worst > r.daily_owner_cap:
            out.append({"skill_id": skill, "owner_ref": owner, "flag": "nagging", "busiest_day": worst})
        if batched >= 5 and batched * 2 >= len(rs):
            out.append({"skill_id": skill, "owner_ref": owner, "flag": "batch_approving", "batched": batched})
    return out


def simulate(rows: list[dict[str, Any]], r: PayoutRules) -> dict[str, Any]:
    """What the rules WOULD pay on these receipts, with nothing written. The smallest experiment from the issue."""
    earned, refused = qualify(rows, r)
    net, fee = split(r.per_outcome_minor, r.fee_bps)
    return {
        "currency": r.currency, "outcomes_paid": len(earned), "refused": refused,
        "creator_minor": net * len(earned), "platform_fee_minor": fee * len(earned),
        "abuse": detect_abuse(rows, r),
    }  # fmt: skip


# --- the ledger -----------------------------------------------------------------------------------------


def creator_of(skill_id: str) -> tuple[str, str] | None:
    """(creator, effect class of the strongest tool) for a community skill, None for ours (nothing is owed)."""
    from superaide.skills import cooldown, registry

    skill = registry.catalog().get(skill_id)
    if skill is None:
        return skill_id.split("/", 1)[0][:80], "read_public"
    if skill.manifest.tier == "official":
        return None
    return skill.manifest.publisher[:80], cooldown.effect_class([t.effect for t in skill.manifest.tools])


def _add(session: Session, **kw: Any) -> PayoutEntry:
    row = PayoutEntry(currency=rules().currency, **kw)
    session.add(row)
    session.flush()
    return row


def bond_balance(session: Session, creator: str) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(PayoutEntry.amount_minor), 0)).where(
                PayoutEntry.creator == creator, PayoutEntry.kind.in_(("bond_deposit", "bond_refund"))
            )
        )
        or 0
    )


def accrue(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Write ledger lines for receipts that earned a payout and clawbacks for reverts, once each."""
    if not enabled():
        return {"accruals": 0, "clawbacks": 0, "skipped_no_bond": 0}
    r = rules()
    rows = receipts.rows(session)
    earned, _ = qualify(rows, r)
    have = {
        (e.kind, e.receipt_seq)
        for e in session.scalars(select(PayoutEntry).where(PayoutEntry.receipt_seq.is_not(None)))
    }
    made = {"accruals": 0, "clawbacks": 0, "skipped_no_bond": 0}
    net, fee = split(r.per_outcome_minor, r.fee_bps)
    by_seq = {x["seq"]: x for x in rows}
    for row in earned:
        who = creator_of(row["skill_id"])
        if who is None or ("accrual", row["seq"]) in have:
            continue
        creator, effect = who
        if effect in BOND_EFFECTS and bond_balance(session, creator) < r.bond_minor:
            made["skipped_no_bond"] += 1
            continue
        at = datetime.fromisoformat(row["at"])
        _add(session, kind="accrual", creator=creator, skill_id=row["skill_id"], version=row["version"],
             receipt_seq=row["seq"], gross_minor=r.per_outcome_minor, fee_minor=fee, amount_minor=net,
             available_at=at + timedelta(days=r.clawback_days))  # fmt: skip
        made["accruals"] += 1
    accrued = {e.receipt_seq: e for e in session.scalars(select(PayoutEntry).where(PayoutEntry.kind == "accrual"))}
    for row in rows:
        orig = by_seq.get(row["ref_seq"]) if row["outcome"] == "reverted" and row["ref_seq"] else None
        entry = accrued.get(orig["seq"]) if orig else None
        if orig is None or entry is None or ("clawback", row["seq"]) in have:
            continue
        if datetime.fromisoformat(row["at"]) - datetime.fromisoformat(orig["at"]) > timedelta(days=r.clawback_days):
            continue  # reverted after the window: the creator keeps it
        _add(session, kind="clawback", creator=entry.creator, skill_id=entry.skill_id, version=entry.version,
             receipt_seq=row["seq"], gross_minor=-entry.gross_minor, fee_minor=-entry.fee_minor,
             amount_minor=-entry.amount_minor, available_at=at_now(now),
             note=f"reverted receipt {orig['seq']}")  # fmt: skip
        made["clawbacks"] += 1
    if made["accruals"] or made["clawbacks"]:
        audit(session, "skills", "payouts_accrued", **made)
    return made


def at_now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def post_bond(session: Session, creator: str, amount_minor: int) -> PayoutEntry:
    """Record that a creator posted a refund bond. A ledger fact only: no money is taken."""
    if not enabled():
        raise PayoutError(404, "Not Found")
    if amount_minor <= 0:
        raise PayoutError(422, "A bond is a positive amount")
    return _add(session, kind="bond_deposit", creator=creator[:80], amount_minor=amount_minor,
                available_at=at_now(None), note="bond posted")  # fmt: skip


def refund_from_bond(session: Session, creator: str, skill_id: str, amount_minor: int, reason: str) -> PayoutEntry:
    """Owe a harmed owner a refund, drawn from this creator's bond and nothing else. The ledger records the debt; a
    settlement provider (the owner's later decision) would carry it out."""
    if not enabled():
        raise PayoutError(404, "Not Found")
    if amount_minor <= 0:
        raise PayoutError(422, "A refund is a positive amount")
    if amount_minor > bond_balance(session, creator):
        raise PayoutError(409, "That is more than the creator's bond holds")
    return _add(session, kind="bond_refund", creator=creator[:80], skill_id=skill_id, amount_minor=-amount_minor,
                available_at=at_now(None), note=reason[:200])  # fmt: skip


def payable(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Per creator: accruals past the clawback window, less every clawback and settlement. Never below zero."""
    now = at_now(now)
    out: dict[str, int] = defaultdict(int)
    for e in session.scalars(select(PayoutEntry).where(PayoutEntry.kind.in_(("accrual", "clawback", "settlement")))):
        if e.kind != "accrual" or e.available_at <= now:
            out[e.creator] += e.amount_minor
    return {c: max(0, v) for c, v in sorted(out.items())}


# --- settlement -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SettlementResult:
    status: str  # settled | not_settled
    moved_minor: int = 0
    note: str = ""


class SettlementProvider(Protocol):
    name: str

    def settle(self, creator: str, amount_minor: int, currency: str, idempotency_key: str) -> SettlementResult: ...


class NullProvider:
    """The only provider. It moves nothing and says so. A real one is the owner's decision (vendor, KYC, fees)."""

    name = "null"

    def settle(self, creator: str, amount_minor: int, currency: str, idempotency_key: str) -> SettlementResult:
        return SettlementResult("not_settled", 0, "no settlement provider is connected; nothing was paid")


PROVIDERS: dict[str, SettlementProvider] = {"null": NullProvider()}


def provider() -> SettlementProvider:
    name = get_settings().payout_provider
    if name not in PROVIDERS:
        raise PayoutError(422, f"Unknown settlement provider {name!r}; only {sorted(PROVIDERS)} exist")
    return PROVIDERS[name]


@dataclass
class SettleReport:
    provider: str
    due: dict[str, int] = field(default_factory=dict)
    settled_minor: int = 0
    notes: list[str] = field(default_factory=list)


def settle_due(
    session: Session, *, now: datetime | None = None, with_provider: SettlementProvider | None = None
) -> SettleReport:
    """Ask the provider to pay what is payable. A `settlement` line is written only for money the provider reports as
    moved, so with the null provider the ledger is unchanged and `settled_minor` is 0."""
    prov = with_provider or provider()
    report = SettleReport(prov.name, payable(session, now=now))
    for creator, amount in report.due.items():
        if amount <= 0:
            continue
        res = prov.settle(creator, amount, rules().currency, f"{creator}:{amount}:{len(report.notes)}")
        report.notes.append(f"{creator}: {res.status}")
        if res.status == "settled" and 0 < res.moved_minor <= amount:
            _add(session, kind="settlement", creator=creator, amount_minor=-res.moved_minor,
                 available_at=at_now(now), note=res.note[:200])  # fmt: skip
            report.settled_minor += res.moved_minor
    return report


def view(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    r = rules()
    entries = session.scalars(select(PayoutEntry).order_by(PayoutEntry.created_at.desc()).limit(200))
    creators = sorted({e.creator for e in session.scalars(select(PayoutEntry))})
    return {
        "currency": r.currency, "provider": get_settings().payout_provider, "moves_money": False,
        "rules": r.__dict__, "payable": payable(session, now=now),
        "bonds": {c: bond_balance(session, c) for c in creators},
        "simulation": simulate(receipts.rows(session), r),
        "entries": [
            {"kind": e.kind, "creator": e.creator, "skill_id": e.skill_id, "receipt_seq": e.receipt_seq,
             "amount_minor": e.amount_minor, "note": e.note, "available_at": e.available_at.isoformat()}
            for e in entries
        ],
    }  # fmt: skip
