"""The introduction steps over HTTP: answers out of order, unknown steps and malformed payloads.

A malformed answer must never be a 500, and must never store junk: it either
gets a hint and stays on the step, or is cleaned to something sensible.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select

from superaide.platform.db.models import Contact, Event, MemoryItem, OwnerPreference, Person
from superaide.platform.db.session import session_scope

VALID: dict[str, dict[str, Any]] = {
    "hello": {"choice": "Nice to meet you"},
    "about": {"mode": "chat", "text": "I run an AI consultancy in Singapore."},
    "people": {"skip": True},
    "urgent": {"topics": ["A family emergency"], "digest": "morning"},
    "reach": {"quiet_start": "22:00", "quiet_end": "07:00", "timezone": "Asia/Singapore"},
    "share": {"skip": True},
    "rules": {},
    "connect": {"choice": "done"},
}
ORDER = list(VALID)


def answer(client, step: str, payload: Any = None):
    return client.post("/app/onboarding", json={"step": step, "answer": {} if payload is None else payload})


def advance_to(client, step: str) -> None:
    for s in ORDER[: ORDER.index(step)] if step != "done" else ORDER:
        r = answer(client, s, VALID[s])
        assert r.status_code == 200, r.text
    assert client.get("/app/onboarding").json()["step"] == step


def _step(client) -> str:
    return client.get("/app/onboarding").json()["step"]


def _events() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(Event)) or 0


def _contacts() -> list[tuple[str, str, bool]]:
    with session_scope() as s:
        rows = s.execute(
            select(Person.display_name, Contact.relationship, Contact.vip).join(Contact, Contact.person_id == Person.id)
        )
        return sorted((n, r, v) for n, r, v in rows)


def _pref() -> OwnerPreference:
    with session_scope() as s:
        return s.scalar(select(OwnerPreference))


def _facts(category: str | None = None) -> list[str]:
    with session_scope() as s:
        q = select(MemoryItem.content).where(MemoryItem.valid_to.is_(None))
        if category:
            q = q.where(MemoryItem.category == category)
        return list(s.scalars(q))


# --- order and unknown steps -------------------------------------------------------------


def test_answering_a_later_step_early_changes_nothing(owner):
    owner.get("/app/onboarding")
    before = _events()
    r = answer(owner, "people", {"people": [{"name": "Priya", "relationship": "Partner", "vip": True}]})
    assert r.status_code == 200 and r.json()["step"] == "hello"  # where we really are
    assert _contacts() == [] and _events() == before


def test_answering_an_earlier_step_again_changes_nothing(owner):
    advance_to(owner, "urgent")
    facts = _facts()
    r = answer(owner, "about", {"mode": "chat", "text": "Actually I'm a pirate."})
    assert r.status_code == 200 and r.json()["step"] == "urgent"
    assert _facts() == facts and not any("pirate" in f for f in facts)


@pytest.mark.parametrize(
    "step", ["bogus", "", "HELLO", "Hello ", "done", pytest.param("x" * 5000, id="5000-chars"), "../hello"]
)
def test_unknown_steps_show_the_current_step_and_store_nothing(owner, step):
    owner.get("/app/onboarding")
    before = _events()
    r = answer(owner, step, VALID["hello"])
    assert r.status_code == 200 and r.json()["step"] == "hello"
    assert _events() == before


def test_answering_after_the_introduction_is_finished(owner):
    """Every step, including "done" itself, once onboarding is over: the done view, never a 500."""
    advance_to(owner, "done")
    before = _events()
    for step in [*ORDER, "done"]:
        r = answer(owner, step, VALID.get(step, {}))
        assert r.status_code == 200, (step, r.text)
        assert r.json()["done"] is True and r.json()["step"] == "done"
    assert _events() == before


# --- malformed requests --------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"answer": {}},
        {"step": 1, "answer": {}},
        {"step": None, "answer": {}},
        {"step": "hello", "answer": ["Nice to meet you"]},
        {"step": "hello", "answer": "Nice to meet you"},
        {"step": "hello", "answer": None},
        [{"step": "hello"}],
    ],
)
def test_malformed_onboarding_requests_are_refused(owner, body):
    owner.get("/app/onboarding")
    r = owner.post("/app/onboarding", json=body)
    assert r.status_code == 422, r.text
    assert _step(owner) == "hello"


def test_a_body_that_is_not_json_is_refused(owner):
    r = owner.post("/app/onboarding", content=b"step=hello", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


@pytest.mark.parametrize("choice", [{"x": 1}, ["a", "b"], 42, None, True, pytest.param("W" * 100_000, id="100k-chars")])
def test_hello_with_odd_choices(owner, choice):
    r = answer(owner, "hello", {"choice": choice})
    assert r.status_code == 200 and r.json()["step"] == "about"
    said = [m["text"] for m in owner.get("/app/messages").json() if m["from"] == "you"]
    assert len(said) == 1 and len(said[0]) <= 100


# --- about you -------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [None, "", "   \n\t ", {"bio": "x"}, ["I", "run"], 0])
def test_about_without_real_text_asks_again_and_stores_nothing(owner, text):
    advance_to(owner, "about")
    r = answer(owner, "about", {"mode": "chat", "text": text})
    assert r.status_code == 200 and r.json()["step"] == "about" and r.json()["hint"]
    assert _facts() == []


def test_about_with_a_huge_text_keeps_a_bounded_amount(owner):
    advance_to(owner, "about")
    r = answer(owner, "about", {"mode": "chat", "text": "I build things. " * 50_000})
    assert r.status_code == 200 and r.json()["step"] == "people"
    assert all(len(f) <= 4100 for f in _facts())


def test_about_continue_with_nothing_shared_asks_first(owner):
    advance_to(owner, "about")
    for mode in ("continue", "video", 7):
        r = answer(owner, "about", {"mode": mode})
        assert r.status_code == 200 and r.json()["step"] == "about" and r.json()["hint"]


# --- your people ------------------------------------------------------------------------------


def test_people_with_empty_names_are_skipped(owner):
    advance_to(owner, "people")
    r = answer(owner, "people", {"people": [{"name": ""}, {"name": "   "}, {"relationship": "Boss"}, {"name": None}]})
    assert r.status_code == 200 and r.json()["step"] == "urgent"
    names = [n for n, _, _ in _contacts()]
    assert names in ([], ["None"]) and "" not in names


def test_people_entries_that_are_not_objects_are_ignored(owner):
    advance_to(owner, "people")
    r = answer(
        owner, "people", {"people": ["Priya", 42, None, ["Arjun", "Team"], {"name": "Mei", "relationship": "Boss"}]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["step"] == "urgent"
    assert _contacts() == [("Mei", "boss", False)]


@pytest.mark.parametrize("people", ["Priya", 42, {"name": "Priya"}, None, True])
def test_a_people_value_that_is_not_a_list_is_a_skip(owner, people):
    advance_to(owner, "people")
    r = answer(owner, "people", {"people": people})
    assert r.status_code == 200 and r.json()["step"] == "urgent"
    assert _contacts() == []


def test_duplicate_names_become_one_contact_with_consistent_facts(owner):
    advance_to(owner, "people")
    r = answer(
        owner,
        "people",
        {
            "people": [
                {"name": "Priya", "relationship": "Partner", "vip": True},
                {"name": "priya", "relationship": "Friend", "vip": False},
                {"name": " Priya ", "relationship": "Boss"},
            ]
        },
    )
    assert r.status_code == 200 and r.json()["step"] == "urgent"
    contacts = _contacts()
    assert len(contacts) == 1
    name, relationship, vip = contacts[0]
    assert name == "Priya" and vip is True
    # The notebook says the same thing as the contact list, once.
    about_priya = [f for f in _facts("person") if "priya" in f.lower()]
    assert len(about_priya) == 1 and relationship in about_priya[0]


def test_names_with_like_wildcards_or_backslashes_are_taken_literally(owner):
    """Names are matched as text, not as SQL LIKE patterns: % and _ match nothing else, and \\ is fine."""
    advance_to(owner, "people")
    r = answer(
        owner,
        "people",
        {
            "people": [
                {"name": "Priya", "relationship": "Partner", "vip": True},
                {"name": "P%", "relationship": "Boss"},
                {"name": "Pri_a", "relationship": "Team"},
                {"name": "Back\\", "relationship": "Friend"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _contacts() == sorted(
        [("Priya", "partner", True), ("P%", "boss", False), ("Pri_a", "team", False), ("Back\\", "friend", False)]
    )


@pytest.mark.parametrize(
    "relationship", ["Nemesis", "", None, 3, ["Boss"], "DROP TABLE contact;", pytest.param("x" * 500, id="500-chars")]
)
def test_an_unknown_relationship_is_stored_as_other(owner, relationship):
    advance_to(owner, "people")
    r = answer(owner, "people", {"people": [{"name": "Arjun", "relationship": relationship}]})
    assert r.status_code == 200
    assert _contacts() == [("Arjun", "other", False)]


@pytest.mark.parametrize("vip", ["false", "no", "0", "", 0, None, [], {}])
def test_a_vip_flag_that_says_no_means_no(owner, vip):
    advance_to(owner, "people")
    r = answer(owner, "people", {"people": [{"name": "Arjun", "relationship": "Team", "vip": vip}]})
    assert r.status_code == 200
    assert _contacts() == [("Arjun", "team", False)]


def test_long_names_and_long_lists_are_bounded(owner):
    advance_to(owner, "people")
    people = [{"name": f"Person {i} " + "x" * 500, "relationship": "Team"} for i in range(100)]
    r = answer(owner, "people", {"people": people})
    assert r.status_code == 200
    contacts = _contacts()
    assert len(contacts) == 30 and all(len(n) <= 120 for n, _, _ in contacts)


# --- urgent -------------------------------------------------------------------------------------


@pytest.mark.parametrize("topics", [None, [], [""], ["   "], [None], [{}], 42, {"a": 1}, True])
def test_urgent_without_a_usable_topic_asks_again(owner, topics):
    advance_to(owner, "urgent")
    r = answer(owner, "urgent", {"topics": topics})
    assert r.status_code == 200, r.text
    assert r.json()["step"] == "urgent" and r.json()["hint"]
    assert _pref().urgent_topics == []


def test_a_topics_string_is_not_split_into_letters(owner):
    advance_to(owner, "urgent")
    r = answer(owner, "urgent", {"topics": "Travel"})
    assert r.status_code == 200
    assert _pref().urgent_topics in ([], ["Travel"])


def test_non_text_topics_are_dropped_not_stored_as_python_reprs(owner):
    advance_to(owner, "urgent")
    r = answer(owner, "urgent", {"topics": ["A family emergency", None, 7, {"x": 1}, ["y"]], "other": None})
    assert r.status_code == 200 and r.json()["step"] == "reach"
    assert _pref().urgent_topics == ["A family emergency"]


def test_urgent_topics_are_bounded_and_the_digest_falls_back(owner):
    advance_to(owner, "urgent")
    r = answer(owner, "urgent", {"topics": ["t" * 1000] * 50, "other": "o" * 1000, "digest": "hourly"})
    assert r.status_code == 200
    pref = _pref()
    assert len(pref.urgent_topics) <= 13 and all(len(t) <= 200 for t in pref.urgent_topics)
    assert pref.digest == "evening"


# --- reaching you -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "times",
    [
        ("25:00", "07:00"),
        ("24:00", "07:00"),
        ("22:00", "7:00"),
        ("22:60", "07:00"),
        ("", "07:00"),
        ("22:00:00", "07:00"),
        (None, "07:00"),
        (2200, "07:00"),
        ("noon", "07:00"),
        ("22:00\n", "07:00"),  # $ in a regex matches before a final newline
        ("1٠:0٠", "07:00"),  # Arabic-Indic digits are \d, but not times
        ("２２:００", "07:00"),  # full-width digits
        ("22:00", " 07:00"),
    ],
)
def test_quiet_hours_must_be_real_times(owner, model, times):
    advance_to(owner, "reach")
    start, end = times
    r = answer(owner, "reach", {"quiet_start": start, "quiet_end": end, "timezone": "Asia/Singapore"})
    assert r.status_code == 200, r.text
    assert r.json()["step"] == "reach" and "22:00" in r.json()["hint"]
    pref = _pref()
    assert (pref.quiet_start, pref.quiet_end) == ("22:00", "07:00")  # unchanged defaults
    # And Ava still works afterwards (her brief parses these times on every turn).
    assert owner.post("/app/chat", json={"text": "hello"}).status_code == 200


@pytest.mark.parametrize(
    "tz",
    [
        "Mars/Phobos",
        "../../etc/passwd",
        "/etc/localtime",
        "",
        "America",
        "zone.tab",
        330,
        None,
        pytest.param("x" * 500, id="500-chars"),
    ],
)
def test_an_invalid_timezone_is_not_stored(owner, tz):
    advance_to(owner, "reach")
    r = answer(owner, "reach", {"quiet_start": "23:00", "quiet_end": "06:30", "timezone": tz})
    assert r.status_code == 200, r.text
    pref = _pref()
    assert pref.timezone == "Asia/Singapore"  # kept what was there
    assert (pref.quiet_start, pref.quiet_end) == ("23:00", "06:30")
    assert owner.post("/app/chat", json={"text": "hello"}).status_code == 200


@pytest.mark.parametrize("channel", ["telegram", "slack", "call", "sms", "", None, 1])
def test_channels_that_arent_available_fall_back_to_the_app(owner, channel):
    advance_to(owner, "reach")
    r = answer(owner, "reach", {**VALID["reach"], "channel": channel})
    assert r.status_code == 200
    assert _pref().interrupt_channel == "app"


@pytest.mark.parametrize("flag", ["false", "no", "0", 0, ""])
def test_vip_breaks_quiet_that_says_no_means_no(owner, flag):
    advance_to(owner, "reach")
    r = answer(owner, "reach", {**VALID["reach"], "vip_breaks_quiet": flag})
    assert r.status_code == 200
    assert _pref().vip_breaks_quiet is False


# --- sharing --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "shareable",
    [
        ["home_address", "phone", "password"],
        "availability",
        42,
        None,
        {"availability": True},
        [None, 1, ["work_email"]],
    ],
)
def test_only_the_offered_share_options_are_stored(owner, shareable):
    advance_to(owner, "share")
    r = answer(owner, "share", {"shareable": shareable})
    assert r.status_code == 200, r.text
    assert r.json()["step"] == "rules"
    assert _pref().shareable == []


def test_share_options_are_stored_once_each(owner):
    advance_to(owner, "share")
    r = answer(owner, "share", {"shareable": ["availability", "availability", "work_email", "nonsense"]})
    assert r.status_code == 200
    assert _pref().shareable == ["availability", "work_email"]


@pytest.mark.parametrize("never", [None, 0, {"x": 1}, ["phone"], False])
def test_a_never_share_that_isnt_text_stores_nothing(owner, never):
    advance_to(owner, "share")
    r = answer(owner, "share", {"shareable": ["availability"], "never": never})
    assert r.status_code == 200
    assert _pref().never_share == ""
    assert _facts("sharing") == []


def test_never_share_is_bounded(owner):
    advance_to(owner, "share")
    r = answer(owner, "share", {"never": "my phone " * 1000})
    assert r.status_code == 200
    assert len(_pref().never_share) <= 1000


# --- the last steps ------------------------------------------------------------------------------


@pytest.mark.parametrize("choice", ["telegram", "slack", None, 5, {"x": 1}])
def test_connect_without_telegram_set_up_just_finishes(owner, choice):
    advance_to(owner, "connect")
    r = answer(owner, "connect", {"choice": choice})
    assert r.status_code == 200 and r.json()["done"] is True
    assert r.json()["input"] == {"kind": "none"}


@pytest.mark.parametrize(
    ("step", "payload"),
    [
        ("about", {"mode": "chat", "text": "I run\x00 a consultancy"}),
        ("people", {"people": [{"name": "Pri\x00ya", "relationship": "Partner"}]}),
        ("urgent", {"topics": ["Travel\x00"]}),
        ("reach", {**VALID["reach"], "timezone": "Asia/Singapore\x00"}),
        ("share", {"never": "my\x00 phone"}),
    ],
    ids=["about", "people", "urgent", "reach", "share"],
)
def test_a_nul_character_in_an_answer_is_refused_not_a_server_error(owner, step, payload):
    advance_to(owner, step)
    r = answer(owner, step, payload)
    assert r.status_code == 422 and "NUL" in r.json()["detail"], r.text
    assert _step(owner) == step
