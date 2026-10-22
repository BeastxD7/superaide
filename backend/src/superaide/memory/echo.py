"""Echo Filter (issue #137): count independent origins for a belief, not repeats.

A belief that arrives from a feed, a newsletter, a forwarded chat or another agent is stored as
`hearsay`, with the set of *root origins* it came from. Ten copies of one rumour have one root and count
once; a second, different root promotes it to `fact`, or the owner can confirm it. Promotion is one SQL
statement, never a judgement by the model. Hearsay stays retrievable but is labelled as such.

Root origin, in order: the oldest link of a quote chain, then the canonical URL, then the author.
First-hand sources (the owner's own chat, files they shared, edits they made) are never hearsay.
Off unless ECHO_FILTER_ENABLED.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit

from sqlalchemy import text
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import MemoryItem

HEARSAY_SOURCES = frozenset({"link", "telegram", "slack"})
PROMOTE_AT = 2  # independent roots needed
_TRACKING = ("utm_", "fbclid", "gclid", "mc_", "ref", "igshid", "spm")


def canonical_url(url: str) -> str:
    """The same page, however it was linked: scheme, `www.`, default port, tracking, fragment and trailing slash gone."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    if not host:
        return url.strip().lower()
    query = sorted((k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(_TRACKING))
    path = parts.path.rstrip("/")
    return f"{host}{path}" + (f"?{urlencode(query)}" if query else "")


def origin_root(*, url: str | None = None, author: str | None = None, quote_chain: Sequence[str] = ()) -> str | None:
    """A short stable id for where a claim started; None when nothing says. `quote_chain` runs newest first
    (A forwarded B who quoted C: [A, B, C]), so the root is its last entry."""
    chain = [c.strip() for c in quote_chain if c and c.strip()]
    if chain:
        basis = "quote:" + (canonical_url(chain[-1]) if chain[-1].lower().startswith("http") else chain[-1].lower())
    elif url and url.strip():
        basis = "url:" + canonical_url(url)
    elif author and author.strip():
        basis = "author:" + " ".join(author.lower().split())
    else:
        return None
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


def record_origin(session: Session, item: MemoryItem, root: str | None) -> int:
    """Note one more place this belief came from (repeats of a known root change nothing), then apply the
    promotion rule. Returns the number of independent roots."""
    if root:
        session.execute(
            text(
                "INSERT INTO memory_origin (id, tenant_id, agent_id, memory_id, origin_root) "
                "VALUES (gen_random_uuid(), :t, :a, :m, :r) ON CONFLICT (memory_id, origin_root) DO NOTHING"
            ),
            {"t": item.tenant_id, "a": item.agent_id, "m": item.id, "r": root},
        )
    n = roots(session, item)
    promote(session, item)
    return n


def roots(session: Session, item: MemoryItem) -> int:
    return int(
        session.scalar(
            text("SELECT count(DISTINCT origin_root) FROM memory_origin WHERE memory_id = :m"), {"m": item.id}
        )
        or 0
    )


def promote(session: Session, item: MemoryItem) -> bool:
    """The SQL promotion rule: hearsay becomes fact once it has PROMOTE_AT distinct roots."""
    done = session.execute(
        text(
            "UPDATE memory_item SET belief_state = 'fact' WHERE id = :m AND belief_state = 'hearsay' "
            "AND (SELECT count(DISTINCT origin_root) FROM memory_origin WHERE memory_id = :m) >= :n"
        ),
        {"m": item.id, "n": PROMOTE_AT},
    ).rowcount
    if done:
        session.refresh(item)
        audit(session, "memory", "hearsay_promoted", memory_id=item.id, by="independent_origins")
    return bool(done)


def confirm(session: Session, item: MemoryItem) -> None:
    """The owner says this is true."""
    if item.belief_state != "fact":
        item.belief_state = "fact"
        audit(session, "memory", "hearsay_promoted", memory_id=item.id, by="owner")


def label(item: MemoryItem) -> str:
    return " · hearsay" if item.belief_state == "hearsay" else ""
