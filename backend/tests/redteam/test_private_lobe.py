"""Private Lobe boundary (#128): only a bool, a listed value or a whole number in range crosses. Everything else,
including free text hidden in every shape an attacker or a chatty local model might try, is refused."""

from __future__ import annotations

import pytest

from superaide.privacy import lobe
from superaide.privacy.lobe import LobeError, Question

CANARY = "Meera Iyer has stage 2 diabetes and owes 41,500 to the clinic"


def refused(envelope) -> str:
    with pytest.raises(LobeError) as exc:
        lobe.receive(envelope)
    return exc.value.code


def test_typed_answers_pass():
    assert lobe.receive({"question": "calendar_blocked_next_tuesday", "answer": True}) == (
        "calendar_blocked_next_tuesday",
        True,
    )
    assert lobe.receive({"question": "monthly_budget_headroom", "answer": "low"})[1] == "low"
    assert lobe.receive({"question": "travel_next_30_days_trips", "answer": 0})[1] == 0
    assert lobe.receive({"question": "travel_next_30_days_trips", "answer": 10})[1] == 10


@pytest.mark.parametrize(
    "answer",
    [CANARY, "yes", "true", "True", 1, 0, None, [True], {"v": True}, 1.0],
)
def test_a_boolean_question_takes_only_a_real_boolean(answer):
    assert refused({"question": "health_appointment_this_week", "answer": answer}) == "not_a_boolean"


@pytest.mark.parametrize(
    "answer",
    [CANARY, "LOW", " low", "low ", "low\n", "l" + chr(0x43E) + "w", "medium; also " + CANARY, True, 1, None, ["low"]],
)
def test_an_enum_question_takes_only_an_exact_listed_value(answer):
    assert refused({"question": "monthly_budget_headroom", "answer": answer}) == "not_a_listed_value"


@pytest.mark.parametrize("answer", [3.0, 2.5, "3", True, False, None, [3], CANARY, float("nan"), float("inf")])
def test_a_range_question_takes_only_a_whole_number(answer):
    assert refused({"question": "travel_next_30_days_trips", "answer": answer}) == "not_a_whole_number"


@pytest.mark.parametrize("answer", [-1, 11, 10**12, -(10**12)])
def test_a_number_outside_the_range_is_refused(answer):
    assert refused({"question": "travel_next_30_days_trips", "answer": answer}) == "outside_the_range"


@pytest.mark.parametrize(
    "envelope",
    [
        None,
        [],
        "answer",
        {},
        {"question": "monthly_budget_headroom"},
        {"answer": "low"},
        {"question": "monthly_budget_headroom", "answer": "low", "note": CANARY},  # a free-text side channel
        {"question": "monthly_budget_headroom", "answer": "low", "explanation": ""},
        {"question": "monthly_budget_headroom", "answer": "low", "": ""},
    ],
)
def test_any_envelope_that_is_not_exactly_question_and_answer_is_refused(envelope):
    assert refused(envelope) == "wrong_shape"


@pytest.mark.parametrize(
    "qid", [CANARY, "", None, 7, ["calendar_blocked_next_tuesday"], "CALENDAR_BLOCKED_NEXT_TUESDAY"]
)
def test_the_cloud_cannot_invent_a_question(qid):
    assert refused({"question": qid, "answer": True}) == "unknown_question"
    with pytest.raises(LobeError):
        lobe.ask(qid)


def test_the_refusal_never_carries_the_value():
    for bad in (CANARY, f"low {CANARY}", {"x": CANARY}):
        for q in lobe.REGISTRY:
            try:
                lobe.receive({"question": q, "answer": bad})
            except LobeError as exc:
                assert CANARY not in str(exc) and CANARY not in repr(exc) and exc.code.isidentifier()


def test_free_text_at_the_boundary_is_counted_and_typed_answers_count_zero():
    ok = {"question": "monthly_budget_headroom", "answer": "low"}
    assert lobe.free_text_fields(ok) == 0
    assert lobe.free_text_fields({"question": "health_appointment_this_week", "answer": True}) == 0
    assert lobe.free_text_fields({**ok, "note": CANARY}) == 2  # the extra key and its text
    assert lobe.free_text_fields({"question": "x", "answer": [CANARY, {"a": "b"}]}) >= 3
    assert lobe.free_text_fields({"answer": float("nan")}) == 1


def test_a_registry_question_cannot_be_built_to_carry_text():
    for bad in (
        lambda: Question("q", "text"),
        lambda: Question("q", "enum", choices=("a",)),
        lambda: Question("q", "enum", choices=("a", "a")),
        lambda: Question("q", "enum", choices=("yes", CANARY)),
        lambda: Question("q", "enum", choices=("yes", "no way!")),
        lambda: Question("q", "range", low=5, high=5),
        lambda: Question("q", "range", low=0, high=10**6),
    ):
        with pytest.raises(ValueError):
            bad()


def test_every_registered_question_is_a_typed_shape():
    for q in lobe.REGISTRY.values():
        assert q.kind in {"bool", "enum", "range"} and q.id == q.id.lower()


def test_nothing_in_the_message_path_uses_the_lobe_yet():
    """The gate is a boundary waiting for its device RPC. Until that exists, no route or pipeline imports it."""
    import pathlib

    root = pathlib.Path(lobe.__file__).resolve().parents[1]
    users = [
        p for p in root.rglob("*.py") if p != pathlib.Path(lobe.__file__).resolve() and "privacy.lobe" in p.read_text()
    ]
    users += [p for p in root.rglob("*.py") if "from superaide.privacy import lobe" in p.read_text()]
    assert users == []
