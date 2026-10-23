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

from superaide.audit import audit
from superaide.config import get_settings
from superaide.db.models import Mandate
from superaide.mandate import token as tk
from superaide.tenancy.context import scope_or_default

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
) -> bool:
    """True when an active mandate (and every ancestor of it) covers this call and it may run without asking.
    Counts the use on the mandate and on each ancestor, so a sub-task's calls come out of its parent's count.
    A helper that was handed a mandate passes its `mandate_id`: only that mandate (and its chain) is considered,
    so a helper holding a narrow child cannot borrow the parent's wider reach."""
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
        # The call must pass every token in the chain (each with its own use count): the narrowest binds.
        if all(
            tk.authorize(
                key, tk.loads(r.token), agent_id=str(scope.agent_id), tool=tool, args=args, now=now, uses=r.uses
            ).ok
            for r in chain
        ):
            for r in chain:
                r.uses += 1
            audit(session, "mandate", "mandate_used", mandate_id=row.id, tool=tool, depth=len(chain))
            return True
    return False


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
