"""Mandates in the gateway (issue #112): sign, list, revoke, and decide whether a call runs without asking.

The ceiling is fixed here, not in a token: a mandate never covers `send` or `spend`, and never covers a
session that has read untrusted input while holding private data (the Rule of Two). Either way the call falls
back to the owner's approval, as it does today.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.mandate import token as tk
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.db.models import Mandate
from superaide.platform.tenancy.context import scope_or_default

NEVER_UNSUPERVISED = {"send", "spend"}


@dataclass(frozen=True)
class Caps:
    """What this session holds. All three together is refused as unsupervised action (Meta's Rule of Two)."""

    untrusted_input: bool
    private_data: bool
    can_act: bool

    @property
    def violates_rule_of_two(self) -> bool:
        return self.untrusted_input and self.private_data and self.can_act


def root_key() -> bytes:
    from superaide.vault.crypto import master_key

    return hmac.new(master_key()[0], b"superaide/mandate/v1", hashlib.sha256).digest()


def create(
    session: Session, owner_id: uuid.UUID, *, tools: list[str], expires: datetime, label: str = "", **limits: Any
) -> Mandate:
    scope = scope_or_default()
    tok = tk.issue(
        root_key(), agent_id=scope.agent_id, issuer=owner_id, tools=tools, expires=expires.isoformat(), **limits
    )
    row = Mandate(token=tk.dumps(tok), card=tk.card(tok), expires_at=expires, label=label.strip()[:120])
    session.add(row)
    session.flush()
    audit(session, "mandate", "mandate_signed", mandate_id=row.id, card=row.card)
    return row


MAX_DEPTH = 3  # owner's mandate, a helper's, a sub-task's


def _children(session: Session, parent_id: uuid.UUID) -> list[Mandate]:
    return list(session.scalars(select(Mandate).where(Mandate.parent_id == parent_id)))


def revoke(session: Session, mandate_id: uuid.UUID) -> None:
    """Revoke a mandate and everything delegated from it, at any depth."""
    row = session.get(Mandate, mandate_id)
    if row is None:
        return
    if row.status == "active":
        row.status = "revoked"
        audit(session, "mandate", "mandate_revoked", mandate_id=row.id)
    for child in _children(session, row.id):
        revoke(session, child.id)


def ancestry(session: Session, row: Mandate) -> list[Mandate]:
    """The row's parents, root first, then the row. Stops (returning what it has) at a missing parent."""
    chain = [row]
    while chain[0].parent_id is not None and len(chain) <= MAX_DEPTH + 1:
        parent = session.get(Mandate, chain[0].parent_id)
        if parent is None:
            return [None, *chain]  # type: ignore[list-item]  # a broken chain: the caller treats None as a no
        chain.insert(0, parent)
    return chain


def depth(session: Session, row: Mandate) -> int:
    return len(ancestry(session, row))


def delegate(
    session: Session,
    parent_id: uuid.UUID,
    *,
    label: str,
    caps: Caps | None = None,
    now: datetime | None = None,
    **narrowing: Any,
) -> Mandate:
    """Hand a mandate on with MORE restrictions (fewer tools, an earlier expiry, a lower count or amount, fewer
    recipient domains or topics). It can never be wider: `token.narrow` refuses, so nothing is stored. A session
    that has read untrusted input cannot hand out authority at all. `narrowing` may only name known limits."""
    now = now or datetime.now(UTC)
    if caps is not None and caps.untrusted_input:
        raise tk.MandateError("a session that has read untrusted content can't hand out authority")
    parent = session.get(Mandate, parent_id)
    if parent is None or parent.status != "active" or parent.expires_at <= now:
        raise tk.MandateError("the mandate to hand on isn't active")
    if depth(session, parent) >= MAX_DEPTH:
        raise tk.MandateError(f"a mandate can be handed on at most {MAX_DEPTH - 1} times")
    if not narrowing:
        raise tk.MandateError("say what to narrow: a child with the same limits would only hide the chain")
    tok = tk.loads(parent.token)
    for name, value in narrowing.items():
        if value is None:
            continue
        tok = tk.narrow(tok, name, value)  # raises MandateError on a widening or an unknown limit
    expires = datetime.fromisoformat(tk.effective(tok)["expires"])
    row = Mandate(
        token=tk.dumps(tok), card=tk.card(tok), expires_at=expires, parent_id=parent.id, label=label.strip()[:120]
    )
    session.add(row)
    session.flush()
    audit(session, "mandate", "mandate_delegated", mandate_id=row.id, parent_id=parent.id, label=row.label)
    return row


def _chain_ok(key: bytes, chain: list[Mandate], agent_id: str, now: datetime) -> bool:
    """Every link is active and unexpired, every token's signature holds, and each child is its parent's token
    with only restrictions added (re-verified here, not trusted from when it was made)."""
    if any(r is None for r in chain):
        return False
    toks = []
    for r in chain:
        if r.status != "active" or r.expires_at <= now:
            return False
        try:
            tok = tk.loads(r.token)
        except tk.MandateError:
            return False
        if not tk.verify_signature(key, tok) or tok.agent_id != agent_id:
            return False
        toks.append(tok)
    return all(tk.never_wider(p, c) for p, c in zip(toks, toks[1:], strict=False))


def permits(
    session: Session,
    *,
    tool: str,
    args: dict[str, Any],
    effect: str,
    caps: Caps,
    now: datetime | None = None,
    mandate_id: uuid.UUID | None = None,
    dry_run: bool = False,
    strict: bool = False,
) -> bool:
    """True when an active mandate (and every ancestor of it) covers this call and it may run without asking.
    Counts the use on the mandate and on each ancestor, so a sub-task's calls come out of its parent's count.
    A helper that was handed a mandate passes its `mandate_id`: only that mandate (and its chain) is considered,
    so a helper holding a narrow child cannot borrow the parent's wider reach.
    `dry_run` (the shadow check) counts nothing, audits nothing, and also says no when a token limits something
    (money, recipients, topics) that the call does not state, since it cannot tell whether the limit holds.
    `strict` (the card skip) does the same refusal for a real call: a narrowed mandate never covers a call whose
    arguments don't say what the narrowing is about."""
    if not get_settings().mandates_enabled:
        return False
    if effect in NEVER_UNSUPERVISED or caps.violates_rule_of_two or caps.untrusted_input:
        return False
    now = now or datetime.now(UTC)
    scope = scope_or_default()
    key = root_key()
    query = select(Mandate).where(Mandate.status == "active", Mandate.expires_at > now)
    if mandate_id is not None:
        query = query.where(Mandate.id == mandate_id)
    for row in session.scalars(query):
        chain = ancestry(session, row)
        if not _chain_ok(key, chain, str(scope.agent_id), now):
            continue
        if (dry_run or strict) and any(_unstated_limit(tk.loads(r.token), args) for r in chain):
            continue
        # The call must pass every token in the chain (each with its own use count): the narrowest binds.
        if all(
            tk.authorize(
                key, tk.loads(r.token), agent_id=str(scope.agent_id), tool=tool, args=args, now=now, uses=r.uses
            ).ok
            for r in chain
        ):
            if dry_run:
                return True
            for r in chain:
                r.uses += 1
            audit(session, "mandate", "mandate_used", mandate_id=row.id, tool=tool, depth=len(chain))
            return True
    return False


# The call argument each limit is checked against (token.authorize).
_LIMIT_ARG = {"max_amount": "amount", "recipient_domains": "recipients", "topics": "topic"}


def _unstated_limit(tok: tk.Token, args: dict[str, Any]) -> bool:
    return any(name in _LIMIT_ARG and _LIMIT_ARG[name] not in args for name, _ in tok.caveats)


# Approval kinds that are a mandate tool under another name, and the effect class of each (tooltrace/effects.py).
# A kind not listed here is never covered: an unknown tool is `send`, which no mandate covers.
SHADOW_TOOL = {"calendar_create_event": "create_event", "calendar_move_event": "move_event"}


def shadow(
    session: Session, *, kind: str, payload: dict[str, Any] | None, tainted: bool = False
) -> dict[str, Any] | None:
    """Would a mandate have let this call run without asking? Only a measurement (issue #112's experiment):
    nothing is spent, counted or skipped, the owner is asked exactly as before. None when it is off."""
    settings = get_settings()
    if not (settings.mandates_enabled and settings.mandate_shadow_enabled):
        return None
    from superaide.tooltrace.effects import effect_of

    payload = payload or {}
    tool = SHADOW_TOOL.get(kind, kind)
    effect = "send" if kind in SHADOW_TOOL and payload.get("notify") else effect_of(tool)  # tells other people
    args: dict[str, Any] = {k: payload[k] for k in ("amount", "recipients", "topic") if k in payload}
    caps = Caps(untrusted_input=tainted, private_data=True, can_act=True)
    try:
        covered = permits(session, tool=tool, args=args, effect=effect, caps=caps, dry_run=True)
    except Exception:  # a measurement must never break the approval it is measuring
        covered = False
    return {"would_cover": covered, "effect": effect, "tainted": tainted}


def shadow_report(session: Session, *, days: int = 30) -> dict[str, Any]:
    """Of the approvals asked in the last `days`, how many a mandate would have covered (the prompts saved)."""
    from datetime import timedelta

    from superaide.platform.db.models import Approval

    since = datetime.now(UTC) - timedelta(days=days)
    total = covered = measured = 0
    for a in session.scalars(select(Approval).where(Approval.created_at >= since)):
        total += 1
        seen = (a.payload or {}).get("mandate_shadow")
        if isinstance(seen, dict):
            measured += 1
            covered += bool(seen.get("would_cover"))
    return {"days": days, "approvals": total, "measured": measured, "would_cover": covered}


def chain_view(session: Session, row: Mandate) -> dict[str, Any]:
    """One mandate for the owner's list: its card, how it narrows its parent, and the chain in words, like
    'Priya scheduling replies, 7 days, 5 msgs -> sub-task: 3 time(s) at most, until ...'."""
    chain = ancestry(session, row)
    live = [r for r in chain if r is not None]
    toks = [tk.loads(r.token) for r in live]
    parts = [f"{live[0].label or 'Mandate'}: {live[0].card}"]
    for parent_tok, child_tok, child in zip(toks, toks[1:], live[1:], strict=False):
        parts.append(f"{child.label or 'sub-task'}: {tk.added_words(parent_tok, child_tok)}")
    return {
        "id": str(row.id),
        "parent_id": str(row.parent_id) if row.parent_id else None,
        "label": row.label,
        "card": row.card,
        "status": row.status,
        "uses": row.uses,
        "expires_at": row.expires_at.isoformat(),
        "depth": len(live) - 1,
        "chain": " -> ".join(parts),
    }


# --- skipping the card (issue #112, MANDATE_SKIP_ENABLED) ---------------------------------------------

# The only tools a mandate can ever let run without a card: your own calendar, nobody else told. Anything else
# (invites, declines, sending, spending, every unlisted tool) keeps asking.
SKIP_TOOLS = {"create_event", "move_event", "gmail_draft_reply"}


def skip_enabled() -> bool:
    s = get_settings()
    return s.mandates_enabled and s.mandate_skip_enabled


def shadow_days(session: Session, *, now: datetime | None = None) -> int:
    """How many whole days ago the shadow check first recorded an approval (0 when it never has)."""
    from sqlalchemy import func

    from superaide.platform.db.models import Approval

    first = session.scalar(select(func.min(Approval.created_at)).where(Approval.payload.has_key("mandate_shadow")))
    if first is None:
        return 0
    return max(0, ((now or datetime.now(UTC)) - first).days)


def skips_card(
    session: Session, *, tool: str, args: dict[str, Any], tainted: bool, notifies: bool, summary: str = ""
) -> bool:
    """May this call run without an approval card because a signed mandate covers it? All of these must hold,
    else the owner is asked as before: the flag, a tool on SKIP_TOOLS, nobody else told, an untainted turn, a stage
    above Infant, shadow data older than MANDATE_SKIP_MIN_DAYS, and an active, unexpired, in-scope mandate chain
    with uses left (`permits`). A use is counted and the skip is audited for the owner's activity."""
    if not skip_enabled() or tool not in SKIP_TOOLS or notifies or tainted:
        return False
    from superaide.triage.store import current_stage
    from superaide.triage.types import Stage

    stage = current_stage(session)
    if stage == Stage.INFANT:
        return False
    if shadow_days(session) < get_settings().mandate_skip_min_days:
        return False
    caps = Caps(untrusted_input=False, private_data=True, can_act=True)
    if not permits(session, tool=tool, args=args, effect="write_external", caps=caps, strict=True):
        return False
    audit(session, "mandate", "mandate_skipped_card", tool=tool, stage=stage.value, summary=summary[:300])
    return True


def recent_skips(session: Session, *, limit: int = 20) -> list[dict[str, Any]]:
    """The owner's activity: calls that ran on a mandate with no card, newest first."""
    from superaide.platform.db.models import AuditLog

    rows = session.scalars(
        select(AuditLog)
        .where(AuditLog.action == "mandate_skipped_card")
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "at": r.created_at.isoformat(),
            "tool": (r.detail or {}).get("tool", ""),
            "summary": (r.detail or {}).get("summary", ""),
        }
        for r in rows
    ]
