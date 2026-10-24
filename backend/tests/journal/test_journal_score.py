"""Calibration is arithmetic: the same rows always give the same numbers, and nothing shows before 10."""

from __future__ import annotations

from superaide.features.journal import score
from superaide.features.journal.extract import Candidate, quoted_by_owner, verify
from superaide.features.journal.score import Resolved


def rows(topic: str, pct: int, hits: int, misses: int) -> list[Resolved]:
    return [Resolved(topic, pct, True)] * hits + [Resolved(topic, pct, False)] * misses


def test_nothing_is_said_before_ten_resolved():
    assert score.calibration(rows("work", 90, 4, 5)) is None
    assert score.calibration(rows("work", 90, 5, 5)) is not None


def test_numbers_are_reproducible_from_the_rows():
    data = rows("timeline", 90, 5, 5) + rows("people", 70, 3, 1) + rows("work", 60, 1, 0)
    first = score.calibration(data)
    assert first == score.calibration(list(reversed(data)))
    assert first is not None
    assert first["resolved"] == 15 and first["right"] == 9 and first["hit_rate"] == 0.6
    # Brier: ten at .9 (5 right), four at .7 (3 right), one at .6 (right)
    expected = (5 * 0.1**2 + 5 * 0.9**2 + 3 * 0.3**2 + 1 * 0.7**2 + 0.4**2) / 15
    assert first["brier"] == round(expected, 3)
    by_from = {b["from"]: b for b in first["buckets"]}
    assert by_from[90]["count"] == 10 and by_from[90]["hit_rate"] == 0.5 and by_from[90]["stated"] == 0.9
    assert by_from[70]["count"] == 4 and by_from[60]["count"] == 1


def test_coaching_names_the_gap_with_the_owners_numbers():
    cal = score.calibration(rows("timeline", 90, 5, 5) + rows("people", 70, 4, 1))
    assert cal is not None
    assert cal["coaching"] == [
        "On timelines you're right 50% of the time when you say you're 90% sure. "
        "Want me to suggest a buffer when you commit?"
    ]
    under = score.calibration(rows("work", 60, 7, 1) + rows("other", 80, 2, 0))
    assert under is not None and "trust yourself a bit more" in under["coaching"][0]


def test_a_topic_with_too_few_predictions_gets_no_coaching():
    cal = score.calibration(rows("timeline", 90, 1, 3) + rows("work", 80, 5, 1))
    assert cal is not None and cal["coaching"] == []


def test_a_quote_must_be_the_owners_own_words():
    message = "I'll ship the beta by 5 Nov, I'm 90% sure. Priya says the client will say yes."
    assert quoted_by_owner("I'll ship the beta by 5 Nov", message)
    assert quoted_by_owner("i'll ship  the beta by 5 NOV", message)  # case and spacing don't matter
    assert not quoted_by_owner("The client will definitely say yes", message)  # reworded: not their words
    assert not quoted_by_owner("yes", message)  # too short to prove anything

    def cand(quote: str) -> Candidate:
        return Candidate(statement="x", quote=quote, confidence_pct=90, inferred=False, topic="timeline")

    kept = verify([cand("I'll ship the beta by 5 Nov"), cand("the CEO will resign next week")], message)
    assert [c.quote for c in kept] == ["I'll ship the beta by 5 Nov"]
