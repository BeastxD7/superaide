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


MIN_REPLAY_LABELS = 30  # below this the comparison says "too few" instead of two noisy numbers


def describe(report: dict[str, Any] | None) -> str:
    """The replay comparison in words, for `superaide triage-knn-compare`. Nothing here decides anything."""
    if report is None:
        return "No embedder is ready (EMBEDDER=none, or the local model is still downloading), so nothing was compared."
    labels, knn, model = report["labels"], report["knn"], report["model"]
    lines = [f"Labelled decisions (your own button presses): {labels}"]
    for name, part in (("kNN head", knn), ("Model's stated score", model)):
        auc_text = "n/a" if part["auc"] is None else f"{part['auc']:.3f}"
        lines.append(f"  {name}: AUC {auc_text} over {part['n']} decisions")
    if labels < MIN_REPLAY_LABELS or knn["auc"] is None:
        lines.append(f"Too few to say anything: needs at least {MIN_REPLAY_LABELS} labels, urgent and not urgent.")
    elif model["auc"] is None:
        lines.append("The model's score was not stored on these decisions, so there is nothing to compare to.")
    else:
        gap = knn["auc"] - model["auc"]
        verdict = "better than" if gap > 0.02 else "worse than" if gap < -0.02 else "about the same as"
        lines.append(f"The kNN head ranks urgency {verdict} the model on your history (AUC gap {gap:+.3f}).")
    lines.append("Each message is scored against all the OTHER labels (leave one out). This changes no decision.")
    return "\n".join(lines)


# --- shadow status: how often the head agrees with the owner's own corrections (#117) ----------------------

SHADOW_MIN_SAMPLE = 200  # corrections with a recorded kNN score needed before promotion is even considered
SHADOW_GATE = 0.8  # the lower end of the Wilson interval on agreement that must be cleared
SHADOW_Z = 1.96  # two-sided 95% (the owner's choice for promotion; the stage gates in promotion.py use 1.645)
SHADOW_WINDOW_DAYS = 30  # only corrections on decisions made in the last 30 days count


def shadow_status(session: Session, *, enabled: bool, now: Any = None) -> dict[str, Any]:
    """Where the head stands, for the owner. It only ever runs in shadow: it records a score beside each
    decision and nothing reads it. Agreement is how often its recorded score (urgent when >= 0.5) matched
    the owner's own label on the same decision; the score was written before the label existed, so it never
    saw the answer. Promotion to deciding is not built; `eligible` only says the evidence would allow asking.
    Below `SHADOW_MIN_SAMPLE` corrections it is never eligible, however high the agreement."""
    from datetime import UTC, datetime, timedelta

    from superaide.triage.promotion import wilson_lower

    since = (now or datetime.now(UTC)) - timedelta(days=SHADOW_WINDOW_DAYS)
    n = agree = 0
    for d, _ in _labelled(session):
        if d.created_at < since:
            continue
        stored = (d.features or {}).get("knn")
        score = stored.get("score") if isinstance(stored, dict) else None
        if not isinstance(score, int | float):
            continue
        n += 1
        agree += (float(score) >= 0.5) == (LABEL_VALUE[str(d.feedback)] == 1.0)
    pct = round(100 * agree / n) if n else None
    lower = wilson_lower(agree, n, SHADOW_Z)
    eligible = enabled and n >= SHADOW_MIN_SAMPLE and lower >= SHADOW_GATE
    if not enabled:
        text = ""
    elif n == 0:
        text = f"Shadow: learning from your corrections, none compared yet, needs {SHADOW_MIN_SAMPLE}."
    else:
        text = (
            f"Shadow: agrees with you {pct}% over {n} correction{'s' if n != 1 else ''}, needs {SHADOW_MIN_SAMPLE}."
            if n < SHADOW_MIN_SAMPLE
            else f"Shadow: agrees with you {pct}% over {n} corrections; it still decides nothing until you say so."
        )
    return {
        "enabled": enabled, "mode": "shadow", "n": n, "agree": agree, "agreement": None if pct is None else pct / 100,
        "lower_bound": round(lower, 3), "needs": SHADOW_MIN_SAMPLE, "gate": SHADOW_GATE, "eligible": eligible,
        "z": SHADOW_Z, "window_days": SHADOW_WINDOW_DAYS, "text": text,
    }  # fmt: skip
