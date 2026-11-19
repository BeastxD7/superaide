# ruff: noqa: E501
"""#644: recall understands time, people and kinds, ranks visitor messages by relevance, and never matches by accident.

The world is the golden set of the retrieval evals (evals/retrieval/golden.yaml): Zeeshan's visitor messages at 26 and
31 days, Meera's invoice message last week and her coffee message yesterday, a colleague's DM, notes and a file. The
privacy tests ask the same questions as a colleague, a stranger and in a group, and as a second instance."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from superaide.evals import retrieval
from superaide.evals.checks import _audience
from superaide.memory.recall import timewords
from superaide.memory.recall.retrieval import ROOM_TITLE, VISITOR_TITLE, recall_scoped, visitor_rows
from superaide.platform.db.session import get_engine, session_scope
from superaide.platform.tenancy.context import InstanceScope, use_instance

TZ = "Asia/Kolkata"


@pytest.fixture
def world() -> retrieval.World:
    return retrieval.build(retrieval.load())


def ask(world: retrieval.World, who: str, query: str, **kw: Any) -> tuple[list[str], Any]:
    aud = _audience(who)
    with session_scope() as s:
        got = recall_scoped(s, aud, query, rooms=who == "owner", asked=query, **kw)
    return [world.names.get(x.uri.rsplit("/", 1)[-1], "?") for x in got.sources], got


# --- the seeded case ---------------------------------------------------------------------------------------


def test_last_week_invoice_is_first_and_the_coffee_message_is_absent(world):
    keys, _ = ask(world, "owner", "what did Meera say last week about the invoice?")
    assert keys[0] == "v_meera_invoice" and "v_meera_coffee" not in keys


def test_yesterday_returns_the_coffee_message_first(world):
    keys, _ = ask(world, "owner", "what did Meera say yesterday")
    assert keys[0] == "v_meera_coffee" and "v_meera_invoice" not in keys


def test_a_message_older_than_thirty_days_is_still_found(world):
    keys, _ = ask(world, "owner", "what did Zeeshan write about the warehouse lease")
    assert keys[0] == "v_zeeshan_lease"


def test_the_model_can_pass_the_filters_itself(world):
    keys, _ = ask(world, "owner", "invoice", person="Meera", since="2000-01-01", kinds=["visitors"])
    assert keys[0] == "v_meera_invoice" and set(keys) <= {"v_meera_invoice", "v_meera_coffee"}
    only_notes, _ = ask(world, "owner", "invoice", kinds=["notes"])
    assert only_notes and all(k.startswith("n_") or k == "?" for k in only_notes)


def test_no_source_kind_is_always_first(world):
    notes_first, _ = ask(world, "owner", "who is our vendor contact at Kreo")
    visitor_first, _ = ask(world, "owner", "what did Zeeshan write about the contract")
    assert notes_first[0].startswith("n_") and visitor_first[0].startswith("v_")


@pytest.mark.parametrize("query", ["yes please", "send", "ok", "yes", "thanks", "please send it now"])
def test_chatter_matches_no_visitor_message_and_does_not_taint(world, query):
    keys, got = ask(world, "owner", query)
    assert not [k for k in keys if k.startswith("v_")]
    assert not any(src.title.startswith((ROOM_TITLE, VISITOR_TITLE)) for src in got.sources)


def test_a_word_in_a_visitor_message_still_finds_it_when_it_is_a_content_word(world):
    keys, _ = ask(world, "owner", "warehouse")
    assert "v_zeeshan_lease" in keys


# --- people ------------------------------------------------------------------------------------------------


def test_a_name_that_matches_two_people_returns_both_labelled():
    golden = retrieval.load()
    golden.world.visitors.append(
        retrieval.Visitor(key="v_meera_das", name="Meera Das", sender="8103", provider="slack", age_days=2,
                          text="Please send the Kreo delivery date.")
    )  # fmt: skip
    golden.world.keys()
    built = retrieval.build(golden)
    keys, got = ask(built, "owner", "what did Meera write")
    assert {"v_meera_invoice", "v_meera_coffee", "v_meera_das"} <= set(keys)
    assert {p.label for p in got.scope.people} == {"Meera Iyer (Telegram)", "Meera Das (Slack)"}
    rendered = " ".join(s.text for s in got.sources)
    assert "Meera Das" in rendered and "Meera Iyer" in rendered


def test_a_name_that_matches_nobody_says_so(world):
    keys, got = ask(world, "owner", "what did Rahul write", person="Rahul")
    assert not [k for k in keys if k.startswith("v_")]
    assert "Nobody called 'Rahul'" in got.nothing(None)


def test_an_empty_filter_says_what_was_searched(world):
    keys, got = ask(world, "owner", "what did Zeeshan write last week")
    assert keys == [] or not [k for k in keys if k.startswith("v_")]
    said = got.nothing(None)
    assert "Zeeshan Khan" in said and "between" in said and "Nothing from" in said


def test_a_typo_in_a_name_still_finds_the_person(world):
    keys, _ = ask(world, "owner", "what did Zeeshn write")
    assert keys and keys[0] in {"v_zeeshan_contract", "v_zeeshan_lease"}


# --- privacy: every filter, every audience ------------------------------------------------------------------

QUESTIONS = [
    ("what did Meera say last week about the invoice?", {}),
    ("what did Zeeshan write", {}),
    ("invoice", {"person": "Meera"}),
    ("invoice", {"since": "2000-01-01", "until": "2100-01-01"}),
    ("invoice sign-off", {"kinds": ["visitors", "messages"]}),
    ("Kreo contract", {"kinds": ["notes", "files", "visitors"]}),
]


@pytest.mark.parametrize("who", ["colleague", "stranger", "group"])
@pytest.mark.parametrize(("query", "args"), QUESTIONS)
def test_no_filter_widens_what_a_lower_audience_may_read(world, who, query, args):
    keys, got = ask(world, who, query, **args)
    allowed = {
        k
        for k, seen in retrieval.load().world.keys().items()
        if ("colleague" if who == "colleague" else "stranger") in seen
    }
    assert set(keys) - {"?"} <= allowed, keys
    assert not any(src.title.startswith((ROOM_TITLE, VISITOR_TITLE)) for src in got.sources)
    assert got.scope.people == [] and got.scope.detailed is False  # no name is resolved, nothing is revealed
    if not got.sources:
        assert got.nothing(None) == "Nothing in your notes or messages matches that."  # does not say who exists


def test_a_colleague_cannot_use_person_to_probe_for_visitors(world):
    for name in ("Meera", "Zeeshan", "Nobody"):
        _, got = ask(world, "colleague", "hello there", person=name)
        assert got.nothing(None) == "Nothing in your notes or messages matches that."


def test_the_owner_gate_is_in_the_sql_even_with_every_filter(world):
    stranger = _audience("stranger")
    with session_scope() as s:
        assert visitor_rows(s, stranger, query="invoice", provider="telegram", since=datetime(2000, 1, 1, tzinfo=UTC),
                            identity_ids={uuid.uuid4()}) == []  # fmt: skip
        assert visitor_rows(s, _audience("colleague"), query="contract") == []


def test_a_second_instance_sees_none_of_the_first_instances_messages(world):
    other = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
    owner = _audience("owner")  # the first instance's owner, asking from inside another instance
    with use_instance(other), session_scope() as s:
        got = recall_scoped(s, owner, "what did Meera say last week about the invoice?", rooms=True)
    assert got.sources == [] and got.scope.people == []


def test_unverified_senders_still_get_public_information_only(world):
    keys, _ = ask(world, "stranger", "what are the office hours")
    assert keys == ["n_office_hours"]
    keys, _ = ask(world, "stranger", "when should I call Meera")
    assert keys == []


# --- the query plan (ADR 0014) ------------------------------------------------------------------------------


def _plan(sql: str) -> str:
    with get_engine().connect() as conn:
        conn.execute(text("SET enable_seqscan = off"))  # a small table: ask what the planner can use
        return "\n".join(r[0] for r in conn.execute(text("EXPLAIN " + sql)))


def test_the_person_time_and_word_filters_use_indexes(world):
    agent = "'" + str(retrieval_agent()) + "'"
    by_time = _plan(f"SELECT id FROM reception_message WHERE agent_id = {agent} AND created_at >= now() - interval '8 days' "
                    "ORDER BY created_at DESC LIMIT 20")  # fmt: skip
    assert "ix_reception_message_agent_created" in by_time or "ix_reception_message_agent_status" in by_time
    by_person = _plan(f"SELECT id FROM reception_message WHERE agent_id = {agent} AND identity_id = '00000000-0000-4000-8000-0000000000aa'")  # fmt: skip
    assert "identity" in by_person and "Seq Scan" not in by_person
    by_words = _plan(
        "SELECT id FROM reception_message WHERE to_tsvector('english', message) @@ to_tsquery('english', 'invoice')"
    )
    assert "ix_reception_message_fts" in by_words
    by_event = _plan(
        f"SELECT id FROM event WHERE agent_id = {agent} AND resolved_person_id = '00000000-0000-4000-8000-0000000000bb' AND created_at >= now() - interval '8 days'"
    )
    assert "ix_event_agent_person_created" in by_event


def retrieval_agent() -> uuid.UUID:
    with session_scope() as s:
        return s.execute(text("SELECT agent_id FROM reception_message LIMIT 1")).scalar_one()


# --- time words, read in the owner's zone ---------------------------------------------------------------------


def _at(y: int, m: int, d: int, hh: int = 9) -> datetime:
    return datetime(y, m, d, hh, tzinfo=ZoneInfo(TZ)).astimezone(UTC)


def _days(span: timewords.Span | None) -> tuple[str, str]:
    assert span is not None and span.since is not None
    z = ZoneInfo(TZ)
    until = span.until.astimezone(z).strftime("%Y-%m-%d %H:%M") if span.until else "-"
    return span.since.astimezone(z).strftime("%Y-%m-%d %H:%M"), until


# 2026-11-16 is a Monday, 2026-11-20 a Friday, 2026-11-22 a Sunday, 2026-11-01 a Sunday.
@pytest.mark.parametrize(
    ("now", "query", "since", "until", "rest"),
    [
        (
            _at(2026, 11, 20),
            "what did Meera say last week about the invoice?",
            "2026-11-09 00:00",
            "2026-11-16 00:00",
            "what did Meera say about the invoice?",
        ),
        (_at(2026, 11, 16), "last week", "2026-11-09 00:00", "2026-11-16 00:00", ""),  # asked on a Monday
        (_at(2026, 11, 22, 23), "last week", "2026-11-09 00:00", "2026-11-16 00:00", ""),  # asked on a Sunday night
        (_at(2026, 11, 16), "this week", "2026-11-16 00:00", "2026-11-23 00:00", ""),
        (_at(2026, 11, 22, 23), "this week", "2026-11-16 00:00", "2026-11-23 00:00", ""),
        (
            _at(2026, 11, 20),
            "what did Meera say yesterday",
            "2026-11-19 00:00",
            "2026-11-20 00:00",
            "what did Meera say",
        ),
        (_at(2026, 11, 1, 0), "yesterday", "2026-10-31 00:00", "2026-11-01 00:00", ""),  # across a month end
        (_at(2026, 1, 1, 8), "last month", "2025-12-01 00:00", "2026-01-01 00:00", ""),  # across a year end
        (_at(2026, 11, 20), "this month", "2026-11-01 00:00", "2026-12-01 00:00", ""),
        (_at(2026, 11, 20), "in October", "2026-10-01 00:00", "2026-11-01 00:00", ""),
        (
            _at(2026, 11, 20),
            "invoice in December",
            "2025-12-01 00:00",
            "2026-01-01 00:00",
            "invoice",
        ),  # not yet: last year's
        (_at(2026, 11, 20), "last Tuesday", "2026-11-17 00:00", "2026-11-18 00:00", ""),
        (_at(2026, 11, 17), "last Tuesday", "2026-11-10 00:00", "2026-11-11 00:00", ""),  # asked on a Tuesday
        (_at(2026, 11, 17), "on Tuesday", "2026-11-17 00:00", "2026-11-18 00:00", ""),
        (_at(2026, 11, 20), "3 days ago", "2026-11-17 00:00", "2026-11-18 00:00", ""),
        (_at(2026, 11, 20), "on 15 November", "2026-11-15 00:00", "2026-11-16 00:00", ""),
        (_at(2026, 11, 20), "2026-11-02", "2026-11-02 00:00", "2026-11-03 00:00", ""),
    ],
)
def test_time_words_become_a_range_in_the_owners_zone(now, query, since, until, rest):
    span, left = timewords.find_span(query, now, TZ)
    assert _days(span) == (since, until)
    assert left == rest


@pytest.mark.parametrize(
    ("query", "since"),
    [
        ("since Monday", "2026-11-16 00:00"),
        ("anything since last week", "2026-11-09 00:00"),
        ("since 2 November", "2026-11-02 00:00"),
        ("since yesterday", "2026-11-19 00:00"),
    ],
)
def test_since_has_no_end(query, since):
    span, _ = timewords.find_span(query, _at(2026, 11, 20), TZ)
    assert span is not None and span.until is None and _days(span)[0] == since


def test_since_monday_asked_on_a_monday_starts_that_day():
    span, _ = timewords.find_span("since Monday", _at(2026, 11, 16, 15), TZ)
    assert _days(span)[0] == "2026-11-16 00:00"


@pytest.mark.parametrize(
    "query", ["may I ask", "march forward", "the weekend plan", "invoice for last", "what is a week"]
)
def test_words_that_are_not_a_time_are_left_alone(query):
    span, left = timewords.find_span(query, _at(2026, 11, 20), TZ)
    assert span is None and left == query


def test_the_zone_decides_where_a_day_begins():
    now = datetime(2026, 11, 20, 20, 0, tzinfo=UTC)  # 01:30 on the 21st in Kolkata, 15:00 on the 20th in New York
    kolkata, _ = timewords.find_span("yesterday", now, "Asia/Kolkata")
    new_york, _ = timewords.find_span("yesterday", now, "America/New_York")
    assert kolkata and new_york and kolkata.since != new_york.since
    assert kolkata.since.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat() == "2026-11-20"
    assert new_york.since.astimezone(ZoneInfo("America/New_York")).date().isoformat() == "2026-11-19"


def test_the_models_own_dates_are_read_in_the_owners_zone():
    assert timewords.parse_date("2026-11-09", TZ) == datetime(2026, 11, 8, 18, 30, tzinfo=UTC)
    assert timewords.parse_date("2026-11-09", TZ, end=True) == datetime(2026, 11, 9, 18, 30, tzinfo=UTC)
    assert timewords.parse_date("last week", TZ) is None and timewords.parse_date("", TZ) is None
