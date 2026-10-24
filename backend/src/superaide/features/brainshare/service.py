"""The share link for the counts-only picture (#55): create, list, revoke, and open by token.

Rules kept here, in code:
- **Counts only.** A visitor gets the same picture the owner can download: fixed topic words and numbers, no
  names, no note text, no file names (app/brain_map.py `share_svg`, `share_counts`).
- **Token hashed at rest, shown once.** Only the sha256 is stored; a lost link is replaced, not recovered.
- **Expires in at most 7 days** (`MAX_DAYS`, and a database check). **Revocable** at any time by the owner.
- **Same answer for everything wrong.** Unknown, forged, expired, revoked and flag-off all answer 404.
- At most `MAX_ACTIVE` live links at once, and a per-token rate limit on visitors.
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.features.brainshare.models import BrainShare
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.tenancy.context import InstanceScope, scope_or_default

TOKEN_PREFIX = "sa_brain_"
MAX_DAYS = 7
MAX_ACTIVE = 5
_calls: dict[uuid.UUID, deque[float]] = defaultdict(deque)


def enabled() -> bool:
    return get_settings().brain_share_enabled


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def status(row: BrainShare, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if row.revoked_at is not None:
        return "revoked"
    return "expired" if row.expires_at <= now else "active"


def view(row: BrainShare, now: datetime | None = None) -> dict[str, object]:
    return {
        "id": str(row.id),
        "created_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "views": row.views,
        "status": status(row, now),
    }


def create(session: Session, owner_id: uuid.UUID, days: int, now: datetime | None = None) -> tuple[BrainShare, str]:
    """A new link and its token, shown once: only the hash is kept."""
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"a link lasts from 1 to {MAX_DAYS} days")
    now = now or datetime.now(UTC)
    here = scope_or_default()
    live = session.scalar(
        select(func.count())
        .select_from(BrainShare)
        .where(
            BrainShare.agent_id == here.agent_id,
            BrainShare.owner_person_id == owner_id,
            BrainShare.revoked_at.is_(None),
            BrainShare.expires_at > now,
        )
    )
    if (live or 0) >= MAX_ACTIVE:
        raise ValueError(f"you already have {MAX_ACTIVE} live links; revoke one first")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = BrainShare(
        tenant_id=here.tenant_id, agent_id=here.agent_id, owner_person_id=owner_id, token_hash=hash_token(token),
        created_at=now, expires_at=now + timedelta(days=days),
    )  # fmt: skip
    session.add(row)
    session.flush()
    audit(session, "owner", "brain_share_created", share_id=row.id, days=days)
    return row, token


def links(session: Session, owner_id: uuid.UUID) -> list[BrainShare]:
    return list(
        session.scalars(
            select(BrainShare)
            .where(BrainShare.agent_id == scope_or_default().agent_id, BrainShare.owner_person_id == owner_id)
            .order_by(BrainShare.created_at.desc())
            .limit(50)
        ).all()
    )


def revoke(session: Session, owner_id: uuid.UUID, share_id: uuid.UUID) -> bool:
    row = session.get(BrainShare, share_id)
    if row is None or row.agent_id != scope_or_default().agent_id or row.owner_person_id != owner_id:
        return False
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        audit(session, "owner", "brain_share_revoked", share_id=row.id)
    return True


def find(session: Session, token: str, now: datetime | None = None) -> BrainShare | None:
    """The live link a token belongs to (looked up by hash, across instances on purpose), or None."""
    if not token.startswith(TOKEN_PREFIX) or len(token) > 200:
        return None
    row = session.scalar(select(BrainShare).where(BrainShare.token_hash == hash_token(token)))
    return row if row is not None and status(row, now) == "active" else None


def scope_of(row: BrainShare) -> InstanceScope:
    return InstanceScope(tenant_id=row.tenant_id, agent_id=row.agent_id)


def within_limit(share_id: uuid.UUID, *, now: float | None = None) -> bool:
    """Sliding one-minute window per link, in this process."""
    t = time.monotonic() if now is None else now
    q = _calls[share_id]
    while q and t - q[0] >= 60:
        q.popleft()
    if len(q) >= get_settings().brain_share_per_minute:
        return False
    q.append(t)
    return True
