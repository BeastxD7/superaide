"""A deletable kNN urgency head (#117): how urgent was the owner's own feedback on the messages
nearest to this one?

It is a shadow score. It is stored in `triage_decision.features["knn"]` for comparison with the
model's score, and nothing in the decision path reads it, so with the flag off (or on) a decision is
exactly what it was.

Why it is safe to delete:
- the labels are the owner's own button presses (`feedback` set by the app, Telegram or Slack, never
  the inferred `useful`), nothing else;
- a label is read through its decision row each time, so taking the feedback back (or erasing the
  decision) removes its influence on the very next score. The cached vector is derived data and is
  cleaned up with it (`forget`);
- it is per instance (Row-Level Security and an `agent_id` filter), and there are no per-owner model
  weights anywhere: just rows.

No new vendor: the embedder is the one recall already uses (local by default). With no embedder
ready, or fewer than `MIN_LABELS` labels, the head says nothing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from superaide.db.models import Event
from superaide.tenancy.context import scope_or_default
from superaide.triage.models import TriageDecision, TriageLabelVector
from superaide.triage.types import Feedback

K = 5
MIN_LABELS = 5  # fewer owner labels than this and the head stays silent
MIN_SIMILARITY = 0.2  # a neighbour less similar than this isn't a neighbour
# What each owner label says about urgency. Held/handled feedback isn't about urgency, so it isn't a label.
LABEL_VALUE: dict[str, float] = {Feedback.NOT_URGENT: 0.0, Feedback.USEFUL: 1.0, Feedback.SHOULD_HAVE_TOLD: 1.0}


@dataclass(frozen=True)
class Neighbour:
    decision_id: str
    similarity: float
    label: str


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def score_from(neighbours: list[tuple[float, float]]) -> float | None:
    """Similarity-weighted mean label of (similarity, label value) pairs; None when there are none."""
    total = sum(s for s, _ in neighbours)
    return None if total <= 0 else sum(s * v for s, v in neighbours) / total


def _labelled(session: Session, *, exclude: Any = None) -> list[tuple[TriageDecision, str]]:
    """The owner's label rows with the message text each is about (owner-attributed only)."""
    stmt = (
        select(TriageDecision, Event.content)
        .join(Event, Event.id == TriageDecision.inbound_event_id)
        .where(
            TriageDecision.agent_id == scope_or_default().agent_id,
            TriageDecision.feedback.in_(list(LABEL_VALUE)),
            TriageDecision.feedback_via.is_not(None),
            TriageDecision.feedback_via != "inferred",
        )
    )
    if exclude is not None:
        stmt = stmt.where(TriageDecision.id != exclude)
    return [(d, text) for d, text in session.execute(stmt).all()]


def _vectors(session: Session, embedder: Any, rows: list[tuple[TriageDecision, str]]) -> dict[Any, list[float]]:
    """Cached vectors for the labelled decisions, embedding (and caching) any that are missing."""
    model = str(getattr(embedder, "name", "embedder"))
    ids = [d.id for d, _ in rows]
    have = {
        v.decision_id: list(v.embedding)
        for v in session.scalars(
            select(TriageLabelVector).where(TriageLabelVector.decision_id.in_(ids), TriageLabelVector.model == model)
        )
    }
    missing = [(d, text) for d, text in rows if d.id not in have]
    if missing:
        vecs = embedder.embed([text for _, text in missing], kind="document")
        if vecs is None:
            return have
        for (d, _), vec in zip(missing, vecs, strict=True):
            have[d.id] = vec
            session.add(TriageLabelVector(decision_id=d.id, model=model, embedding=vec))
        session.flush()
    return have


def knn_urgency(
    session: Session, embedder: Any, text: str, *, k: int = K, exclude: Any = None
) -> dict[str, Any] | None:
    """{"score", "n_labels", "neighbours": [...]} for one message, or None when the head can't say."""
    if embedder is None or not embedder.ready() or not text.strip():
        return None
    rows = _labelled(session, exclude=exclude)
    if len(rows) < MIN_LABELS:
        return None
    query = embedder.embed([text], kind="query")
    if not query:
        return None
    vectors = _vectors(session, embedder, rows)
    scored = sorted(
        ((cosine(query[0], vectors[d.id]), d) for d, _ in rows if d.id in vectors),
        key=lambda p: p[0],
        reverse=True,
    )
    top = [(s, d) for s, d in scored[:k] if s >= MIN_SIMILARITY]
    if not top:
        return None
    value = score_from([(s, LABEL_VALUE[str(d.feedback)]) for s, d in top])
    if value is None:
        return None
    return {
        "score": round(value, 4),
        "n_labels": len(rows),
        "neighbours": [{"decision_id": str(d.id), "similarity": round(s, 4), "label": str(d.feedback)} for s, d in top],
    }


def forget(session: Session, decision_id: Any) -> int:
    """Drop the cached vector of a label the owner took back. Returns the rows removed."""
    res = session.execute(delete(TriageLabelVector).where(TriageLabelVector.decision_id == decision_id))
    return int(getattr(res, "rowcount", 0) or 0)


def shadow_score(session: Session, text: str, *, exclude: Any = None) -> dict[str, Any] | None:
    """The kNN score for `features["knn"]`, or None. Never raises: a shadow score must not touch a decision."""
    import logging

    try:
        from superaide.brain.embed import get_embedder
        from superaide.config import get_settings

        with session.begin_nested():
            return knn_urgency(session, get_embedder(get_settings()), text, exclude=exclude)
    except Exception:
        logging.getLogger(__name__).exception("kNN triage head failed")
        return None


def auc(pairs: list[tuple[float, bool]]) -> float | None:
    """Probability that a random urgent item scores above a random not-urgent one (ties count half)."""
    pos = [s for s, y in pairs if y]
    neg = [s for s, y in pairs if not y]
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def compare(session: Session, embedder: Any, *, k: int = K) -> dict[str, Any] | None:
    """On the owner's own labelled history, leave one out: AUC of this head against the model's stored score
    (`features["score"]`), for the decisions that have both. Offline and free once the vectors are cached."""
    if embedder is None or not embedder.ready():
        return None
    rows = _labelled(session)
    vectors = _vectors(session, embedder, rows)
    knn_pairs: list[tuple[float, bool]] = []
    model_pairs: list[tuple[float, bool]] = []
    for d, _ in rows:
        if d.id not in vectors:
            continue
        near = sorted(
            ((cosine(vectors[d.id], vectors[o.id]), o) for o, _ in rows if o.id != d.id and o.id in vectors),
            key=lambda p: p[0],
            reverse=True,
        )
        top = [(s, o) for s, o in near[:k] if s >= MIN_SIMILARITY]
        value = score_from([(s, LABEL_VALUE[str(o.feedback)]) for s, o in top])
        urgent = LABEL_VALUE[str(d.feedback)] == 1.0
        if value is not None:
            knn_pairs.append((value, urgent))
        stated = (d.features or {}).get("score")
        if isinstance(stated, int | float):
            model_pairs.append((float(stated), urgent))
    return {
        "labels": len(rows),
        "knn": {"n": len(knn_pairs), "auc": auc(knn_pairs)},
        "model": {"n": len(model_pairs), "auc": auc(model_pairs)},
    }
