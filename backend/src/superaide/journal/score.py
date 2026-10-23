"""Calibration is arithmetic over stored rows. No model is involved, so the same rows always give the same numbers.

A prediction is a statement the owner made with a confidence (50 to 99 percent) that later came true or not.
Calibration compares how sure the owner said they were with how often they turned out right.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

MIN_RESOLVED = 10  # the calibration page opens after this many resolved predictions
MIN_TOPIC = 5  # a coaching line needs this many resolved predictions on the topic
GAP = 0.15  # stated confidence and hit rate must differ by at least this much to say so
TOPIC_WORD = {
    "timeline": "timelines",
    "people": "people decisions",
    "money": "money",
    "work": "work",
    "other": "other calls",
}


@dataclass(frozen=True)
class Resolved:
    topic: str
    confidence_pct: int
    came_true: bool


def calibration(rows: list[Resolved]) -> dict[str, Any] | None:
    """None until there are enough resolved predictions to say anything honest."""
    if len(rows) < MIN_RESOLVED:
        return None
    n = len(rows)
    hits = sum(r.came_true for r in rows)
    brier = sum((r.confidence_pct / 100 - (1 if r.came_true else 0)) ** 2 for r in rows) / n
    buckets: dict[int, list[Resolved]] = defaultdict(list)
    for r in rows:
        buckets[min(90, r.confidence_pct // 10 * 10)].append(r)  # 50s, 60s, 70s, 80s, 90s
    chart = [
        {
            "from": lo,
            "to": lo + 9,
            "count": len(items),
            "stated": round(sum(i.confidence_pct for i in items) / len(items) / 100, 3),
            "hit_rate": round(sum(i.came_true for i in items) / len(items), 3),
        }
        for lo, items in sorted(buckets.items())
    ]
    by_topic: dict[str, list[Resolved]] = defaultdict(list)
    for r in rows:
        by_topic[r.topic].append(r)
    coaching: list[str] = []
    for topic, items in sorted(by_topic.items()):
        if len(items) < MIN_TOPIC:
            continue
        stated = sum(i.confidence_pct for i in items) / len(items) / 100
        hit = sum(i.came_true for i in items) / len(items)
        word = TOPIC_WORD.get(topic, topic)
        if stated - hit >= GAP:
            coaching.append(
                f"On {word} you're right {round(hit * 100)}% of the time when you say you're "
                f"{round(stated * 100)}% sure. Want me to suggest a buffer when you commit?"
            )
        elif hit - stated >= GAP:
            coaching.append(
                f"On {word} you're right {round(hit * 100)}% of the time when you say you're "
                f"{round(stated * 100)}% sure. You can trust yourself a bit more there."
            )
    return {
        "resolved": n,
        "right": hits,
        "hit_rate": round(hits / n, 3),
        "brier": round(brier, 3),
        "buckets": chart,
        "coaching": coaching,
    }
