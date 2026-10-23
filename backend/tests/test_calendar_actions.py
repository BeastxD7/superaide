"""Google Calendar actions: free/busy, add, move and decline events, the consent and incremental
scope upgrade that switch them on, approval routing by effect class (ADR 0006) and per-instance
isolation (ADR 0009). Google is a mock: never the real one."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from superaide.app import connections
from superaide.app.accounts import owner as hired_owner
from superaide.brain.loop import narrate
from superaide.connectors import google
from superaide.db.models import Approval, AuditLog
from superaide.db.session import session_scope
from superaide.main import create_app
from superaide.policy.killswitch import set_state
from superaide.skills import calendar, registry
from superaide.tenancy.context import InstanceScope, use_instance

PASSWORD = "correct horse battery"
CLIENT = "1234-cal.apps.googleusercontent.com"
SECRET = "test-cal-client-secret-9z"
READ = "https://www.googleapis.com/auth/calendar.readonly"
EVENTS = google.EVENTS_SCOPE
SKILL = "/app/skills/superaide/google-calendar"


class FakeCalendar:
    """Google's token endpoint and Calendar API, recording every request."""

    def __init__(self) -> None:
        self.granted = READ
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
        self.events: dict[str, dict[str, Any]] = {}
        self.busy: list[dict[str, str]] = []
        self.fail_with: httpx.Response | None = None  # every Calendar API call answers this

    def writes(self) -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
        return [c for c in self.calls if c[0] in {"POST", "PATCH"} and "/events" in c[1] and "watch" not in c[1]]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        params = dict(url.params)
        body: dict[str, Any] = {}
        if request.content and request.headers.get("content-type", "").startswith("application/json"):
            body = json.loads(request.content)
        self.calls.append((request.method, str(url.copy_with(query=None)), params, body))
        if str(url).startswith(google.TOKEN_URL):
            form = parse_qs(request.content.decode())
            if form.get("grant_type") == ["authorization_code"]:
                return httpx.Response(
                    200, json={"access_token": "ya29.x", "refresh_token": "1//r", "scope": self.granted}
                )
            return httpx.Response(200, json={"access_token": "ya29.x", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer ya29.x"
        if self.fail_with is not None:
            return self.fail_with
        path = url.path
        if path.endswith("/freeBusy"):
            return httpx.Response(200, json={"calendars": {"primary": {"busy": self.busy}}})
        if request.method == "POST" and path.endswith("/events"):
            made = {**body, "id": f"new{len(self.events) + 1}"}
            self.events[made["id"]] = made
            return httpx.Response(200, json=made)
        event_id = path.rsplit("/", 1)[1]
        if event_id not in self.events:
            return httpx.Response(404, json={"error": {"code": 404}})
        if request.method == "GET":
            return httpx.Response(200, json=self.events[event_id])
        if request.method == "PATCH":
            self.events[event_id] = {**self.events[event_id], **body}
            return httpx.Response(200, json=self.events[event_id])
        return httpx.Response(400)


@pytest.fixture
def fake(monkeypatch) -> FakeCalendar:
    f = FakeCalendar()
    transport = httpx.MockTransport(f)
    monkeypatch.setattr(google, "_client", lambda client=None: client or httpx.Client(transport=transport))
    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=transport))
    google._cache.clear()
    yield f
    google._cache.clear()


@pytest.fixture
def app(agent):
    return create_app(agent)


def _owner(app, name: str, email: str) -> TestClient:
    c = TestClient(app, headers={"X-Requested-With": "superaide"})
    assert c.post("/auth/signup", json={"name": name, "email": email, "password": PASSWORD}).status_code == 200
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def scope_of(email: str) -> InstanceScope:
    with session_scope() as s:
        row = s.execute(
            text("SELECT i.tenant_id, i.id FROM agent_instance i JOIN instance_member m ON m.agent_id = i.id "
                 "JOIN user_account u ON u.id = m.user_id WHERE u.email_normalized = :e"),
            {"e": email},
        ).one()  # fmt: skip
        return InstanceScope(tenant_id=row[0], agent_id=row[1])


def connect(c: TestClient, fake: FakeCalendar) -> None:
    """Install the skill and connect Google read-only, the way an owner does today."""
    assert c.post(f"{SKILL}/install", json={"accepted": ["read_private"]}).status_code == 200
    assert c.post("/app/connections/google", json={"client_id": CLIENT, "client_secret": SECRET}).status_code == 200
    q = parse_qs(urlparse(c.post("/app/connections/google/start").json()["url"]).query)
    fake.granted = READ
    r = c.get("/app/connections/google/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert r.headers["location"].endswith("?google=connected")


def switch_on(c: TestClient, fake: FakeCalendar) -> dict[str, Any]:
    """Allow calendar actions in Skills and finish Google's incremental consent."""
    card = c.post(f"{SKILL}/options", json={"setting": "actions", "on": True, "accepted": ["write_external", "send"]})
    assert card.status_code == 200, card.text
    url = card.json()["google_url"]
    q = parse_qs(urlparse(url).query)
    assert set(q["scope"][0].split()) == {READ, EVENTS} and q["include_granted_scopes"] == ["true"]
    fake.granted = f"{READ} {EVENTS}"
    r = c.get("/app/connections/google/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert r.headers["location"] == "http://localhost:3000/dashboard/capabilities?google=connected"
    return card.json()


@pytest.fixture
def owner(app, fake) -> TestClient:
    c = _owner(app, "Shashank Rao", "s@example.com")
    connect(c, fake)
    return c


def run(email: str, name: str, args: dict[str, Any], *, tainted: bool = False, relation: str = "manager"):
    with use_instance(scope_of(email)), session_scope() as s:
        person = hired_owner(s)
        sender = SimpleNamespace(person=person, relation=relation, trust=3, name=person.display_name)
        return calendar.run_tool(s, sender, name, args, tainted=tainted)


def events_tool(email: str, args: dict[str, Any]):
    with use_instance(scope_of(email)), session_scope() as s:
        return calendar.calendar_events(s, hired_owner(s), args)


def pending(email: str) -> list[Approval]:
    with use_instance(scope_of(email)), session_scope() as s:
        rows = s.scalars(select(Approval).where(Approval.status == "pending").order_by(Approval.created_at)).all()
        for r in rows:
            s.expunge(r)
        return list(rows)


def option(c: TestClient) -> dict[str, Any]:
    card = next(x for x in c.get("/app/skills").json() if x["id"] == calendar.SKILL_ID)
    return card["options"][0]


def tool_names(email: str) -> set[str]:
    with use_instance(scope_of(email)), session_scope() as s:
        skills = registry.active(s)
        return {
            t["name"] for t in registry.tools_for(skills, web_allowed=False, options=registry.options_on(s, skills))
        }


# --- consent and the scope upgrade ------------------------------------------------------------------


def test_actions_are_off_until_the_owner_agrees_and_google_grants_calendar_events(owner, fake):
    o = option(owner)
    assert o["on"] is False and o["tools"] == ["create_event", "move_event", "decline_event"]
    assert [p["id"] for p in o["permissions"]] == ["write_external", "send"]
    assert {"create_event", "move_event", "decline_event"}.isdisjoint(tool_names("s@example.com"))
    assert "calendar_free_busy" in tool_names("s@example.com")  # reading is part of the install
    # A consent that doesn't match what the screen showed is refused.
    r = owner.post(f"{SKILL}/options", json={"setting": "actions", "on": True, "accepted": ["send"]})
    assert r.status_code == 409 and option(owner)["on"] is False
    assert owner.post(f"{SKILL}/options", json={"setting": "nope", "on": True}).status_code == 404
    card = switch_on(owner, fake)
    assert card["options"][0]["on"] is True and card["options"][0]["needs_google"] is True  # before Google said yes
    assert option(owner)["needs_google"] is False
    assert {"create_event", "move_event", "decline_event"} <= tool_names("s@example.com")
    # Reconnecting from Connections keeps asking for calendar.events while actions are on.
    q = parse_qs(urlparse(owner.post("/app/connections/google/start").json()["url"]).query)
    assert EVENTS in q["scope"][0]


def test_declining_the_calendar_events_box_leaves_actions_unusable(owner, fake):
    card = owner.post(
        f"{SKILL}/options", json={"setting": "actions", "on": True, "accepted": ["write_external", "send"]}
    )
    q = parse_qs(urlparse(card.json()["google_url"]).query)
    fake.granted = READ  # the owner unticked "change your calendar" on Google's screen
    r = owner.get(
        "/app/connections/google/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False
    )
    assert r.headers["location"].endswith("/dashboard/capabilities?google=no_actions")
    assert option(owner)["needs_google"] is True
    out, err = run("s@example.com", "create_event", {"title": "Gym", "when": "tomorrow at 7am"})
    assert err and "permission to change the calendar" in out and fake.writes() == []


def test_switching_actions_off_removes_the_tools_and_the_grant(owner, fake):
    switch_on(owner, fake)
    r = owner.post(f"{SKILL}/options", json={"setting": "actions", "on": False})
    assert r.status_code == 200 and r.json()["options"][0]["on"] is False
    assert "create_event" not in tool_names("s@example.com")
    out, err = run("s@example.com", "create_event", {"title": "Gym", "when": "tomorrow at 7am"})
    assert err and "Calendar actions are off" in out
    with use_instance(scope_of("s@example.com")), session_scope() as s:
        row = registry._rows(s)[calendar.SKILL_ID]
        assert row.granted == ["read_private"]


def test_options_route_is_owner_only(app, owner, fake):
    stranger = TestClient(app, headers={"X-Requested-With": "superaide"})
    assert stranger.post(f"{SKILL}/options", json={"setting": "actions", "on": True}).status_code == 401
    stranger.post("/auth/signup", json={"name": "Mallory", "email": "m@example.com", "password": PASSWORD})
    assert stranger.post(f"{SKILL}/options", json={"setting": "actions", "on": True}).status_code == 403


# --- the tools ----------------------------------------------------------------------------------------


def test_free_busy_lists_busy_and_free_hours(owner, fake):
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).astimezone(UTC).date()
    with use_instance(scope_of("s@example.com")), session_scope() as s:
        tz = calendar.zone(calendar.owner_zone(s, hired_owner(s)))
    nine = datetime.combine(tomorrow, datetime.min.time(), tz).replace(hour=9)
    fake.busy = [{"start": nine.isoformat(), "end": (nine + timedelta(hours=1)).isoformat()}]
    out, err = run("s@example.com", "calendar_free_busy", {"from": "tomorrow"})
    assert not err and "busy 09:00–10:00" in out and "free 08:00–09:00, 10:00–20:00" in out


def test_a_private_event_is_added_at_once_without_emailing_anyone(owner, fake):
    switch_on(owner, fake)
    out, err = run("s@example.com", "create_event", {"title": "Gym", "when": "tomorrow at 7am", "minutes": 45})
    assert not err and out.startswith("Added “Gym”")
    [(method, _, params, body)] = fake.writes()
    assert method == "POST" and params["sendUpdates"] == "none" and "attendees" not in body
    assert pending("s@example.com") == []


def test_inviting_others_waits_for_approval_and_approving_sends_the_invites(owner, fake):
    switch_on(owner, fake)
    args = {"title": "Lunch", "when": "tomorrow at 1pm", "attendees": ["Priya@Example.com"]}
    out, err = run("s@example.com", "create_event", args)
    assert not err and "Waiting for the owner's approval" in out and fake.writes() == []
    [approval] = pending("s@example.com")
    assert approval.kind == "calendar_create_event" and "priya@example.com" in approval.payload["summary"]
    listed = owner.get("/app/approvals").json()
    assert listed[0]["kind"] == "calendar_create_event" and "Lunch" in listed[0]["summary"]
    r = owner.post(f"/app/approvals/{approval.id}/approve")
    assert r.status_code == 200 and r.json()["result"] == "Added to your calendar."
    [(method, _, params, body)] = fake.writes()
    assert params["sendUpdates"] == "all" and body["attendees"] == [{"email": "priya@example.com"}]


def test_rejecting_changes_nothing(owner, fake):
    switch_on(owner, fake)
    run("s@example.com", "create_event", {"title": "Lunch", "when": "tomorrow at 1pm", "attendees": ["p@example.com"]})
    [approval] = pending("s@example.com")
    assert owner.post(f"/app/approvals/{approval.id}/reject").status_code == 200
    assert fake.writes() == []


def test_after_web_content_even_a_private_event_waits_for_approval(owner, fake):
    switch_on(owner, fake)
    out, _ = run("s@example.com", "create_event", {"title": "Gym", "when": "tomorrow at 7am"}, tainted=True)
    assert "Waiting for the owner's approval" in out and fake.writes() == []


def test_at_a_later_stage_a_private_event_still_runs_and_invites_still_wait_at_child(owner, fake):
    switch_on(owner, fake)
    with use_instance(scope_of("s@example.com")), session_scope() as s:
        set_state(s, "stage", {"stage": "child"})
    run("s@example.com", "create_event", {"title": "Lunch", "when": "tomorrow at 1pm", "attendees": ["p@example.com"]})
    assert len(pending("s@example.com")) == 1
    with use_instance(scope_of("s@example.com")), session_scope() as s:
        set_state(s, "stage", {"stage": "adult"})
    out, _ = run("s@example.com", "create_event", {"title": "Lunch 2", "when": "tomorrow at 2pm",
                                                   "attendees": ["p@example.com"]})  # fmt: skip
    assert out.startswith("Added") and len(fake.writes()) == 1


def _seed(fake: FakeCalendar, event_id: str, others: bool, *, mine: bool = True) -> None:
    start = datetime.now(UTC) + timedelta(days=2)
    attendees = [{"email": "me@example.com", "self": True}] if (others or not mine) else []
    if others:
        attendees.append({"email": "priya@example.com", "displayName": "Priya"})
    fake.events[event_id] = {
        "id": event_id, "summary": "Sync", "status": "confirmed", "attendees": attendees,
        "start": {"dateTime": start.isoformat()}, "end": {"dateTime": (start + timedelta(minutes=45)).isoformat()},
    }  # fmt: skip


def test_moving_a_private_event_runs_and_keeps_its_length(owner, fake):
    switch_on(owner, fake)
    _seed(fake, "ev1", others=False)
    out, err = run("s@example.com", "move_event", {"id": "ev1", "when": "Friday at 4pm"})
    assert not err and out.startswith("Moved “Sync”")
    [(method, url, params, body)] = fake.writes()
    assert method == "PATCH" and url.endswith("/events/ev1") and params["sendUpdates"] == "none"
    s, e = datetime.fromisoformat(body["start"]["dateTime"]), datetime.fromisoformat(body["end"]["dateTime"])
    assert e - s == timedelta(minutes=45)


def test_moving_a_meeting_with_others_waits_then_tells_them(owner, fake):
    switch_on(owner, fake)
    _seed(fake, "ev2", others=True)
    out, _ = run("s@example.com", "move_event", {"id": "ev2", "when": "Friday at 4pm"})
    assert "Waiting" in out and "priya@example.com" in out and fake.writes() == []
    [approval] = pending("s@example.com")
    assert owner.post(f"/app/approvals/{approval.id}/approve").json()["result"] == "Moved."
    [(method, _, params, _)] = fake.writes()
    assert method == "PATCH" and params["sendUpdates"] == "all"


def test_declining_always_waits_and_approving_declines_with_the_note(owner, fake):
    switch_on(owner, fake)
    with use_instance(scope_of("s@example.com")), session_scope() as s:
        set_state(s, "stage", {"stage": "adult"})  # even at the last stage: declining sends a reply
    _seed(fake, "ev3", others=True)
    out, _ = run("s@example.com", "decline_event", {"id": "ev3", "note": "Clash, sorry"})
    assert "Waiting" in out and fake.writes() == []
    [approval] = pending("s@example.com")
    assert approval.kind == "calendar_decline_event"
    assert owner.post(f"/app/approvals/{approval.id}/approve").status_code == 200
    [(_, _, params, body)] = fake.writes()
    me = next(a for a in body["attendees"] if a.get("self"))
    assert params["sendUpdates"] == "all" and me["responseStatus"] == "declined" and me["comment"] == "Clash, sorry"
    other = next(a for a in body["attendees"] if not a.get("self"))
    assert "responseStatus" not in other


def test_declining_your_own_event_or_a_gone_event_says_so(owner, fake):
    switch_on(owner, fake)
    _seed(fake, "own", others=False)
    out, err = run("s@example.com", "decline_event", {"id": "own"})
    assert err and "nothing to decline" in out
    out, err = run("s@example.com", "decline_event", {"id": "missing"})
    assert err and "isn't in the calendar" in out
    assert pending("s@example.com") == []


def test_bad_times_and_addresses_are_explained_not_guessed(owner, fake):
    switch_on(owner, fake)
    out, err = run("s@example.com", "create_event", {"title": "X", "when": "whenever"})
    assert err and "Couldn't read the time" in out
    out, err = run("s@example.com", "create_event", {"title": "X", "when": "tomorrow 3pm", "attendees": ["priya"]})
    assert err and "isn't an email address" in out
    assert fake.writes() == [] and pending("s@example.com") == []


def test_only_the_owner_may_change_the_calendar(owner, fake):
    switch_on(owner, fake)
    out, err = run("s@example.com", "create_event", {"title": "X", "when": "tomorrow 3pm"}, relation="colleague")
    assert err and "Only the owner" in out and fake.writes() == []


def test_approving_after_disconnecting_changes_nothing_and_says_why(owner, fake):
    switch_on(owner, fake)
    run("s@example.com", "create_event", {"title": "Lunch", "when": "tomorrow at 1pm", "attendees": ["p@example.com"]})
    [approval] = pending("s@example.com")
    owner.post("/app/connections/google/disconnect")
    r = owner.post(f"/app/approvals/{approval.id}/approve")
    assert r.status_code == 200 and "Nothing changed" in r.json()["result"] and fake.writes() == []


def test_narration_labels_for_the_live_work_log():
    assert narrate("calendar_free_busy", {"from": "Friday"}) == "Checking when you're free: Friday"
    assert narrate("create_event", {"title": "Lunch", "when": "tomorrow 1pm", "attendees": ["p@example.com"]}) == (
        "Adding to your calendar: Lunch, tomorrow 1pm, inviting p@example.com"
    )
    assert narrate("move_event", {"when": "Friday 4pm"}) == "Moving a calendar event to Friday 4pm"
    assert narrate("decline_event", {}) == "Declining a calendar invitation (needs your OK)"


# --- each owner's own ---------------------------------------------------------------------------------


def test_one_owners_actions_approvals_and_calendar_are_theirs_alone(app, owner, fake):
    switch_on(owner, fake)
    run("s@example.com", "create_event", {"title": "Lunch", "when": "tomorrow at 1pm", "attendees": ["p@example.com"]})
    [approval] = pending("s@example.com")
    other = _owner(app, "Priya Tan", "p@example.com")
    assert other.post(f"{SKILL}/install", json={"accepted": ["read_private"]}).status_code == 200
    # B can't see or approve A's approval, and has no calendar actions of their own.
    assert all(a["id"] != str(approval.id) for a in other.get("/app/approvals").json())
    assert other.post(f"/app/approvals/{approval.id}/approve").status_code == 404
    assert option(other)["on"] is False and "create_event" not in tool_names("p@example.com")
    out, err = run("p@example.com", "create_event", {"title": "X", "when": "tomorrow 3pm"})
    assert err and "isn't connected" in out
    assert pending("p@example.com") == [] and fake.writes() == []
    with use_instance(scope_of("s@example.com")), session_scope() as s:
        assert s.get(Approval, approval.id).status == "pending"
    with use_instance(scope_of("p@example.com")), session_scope() as s:
        assert s.get(Approval, approval.id) is None
        assert not s.scalars(select(AuditLog).where(AuditLog.action == "skill_option_on")).all()
    assert uuid.UUID(str(approval.id))


def test_in_a_real_turn_the_model_gets_the_tools_and_an_invite_becomes_an_approval(owner, fake):
    from tests.test_loop import Script, tool_use, turn_for

    switch_on(owner, fake)
    args = {"title": "Lunch", "when": "tomorrow at 1pm", "attendees": ["p@example.com"]}
    script = Script(tool_use("create_event", args), "It's waiting for your OK.")
    with use_instance(scope_of("s@example.com")), session_scope() as s:
        turn = turn_for(s, script, "invite Priya to lunch tomorrow at 1")
        names = {t["name"] for t in turn.tools()}
        assert {"calendar_events", "calendar_free_busy", "create_event", "move_event", "decline_event"} <= names
        propose = next(t for t in turn.tools() if t["name"] == "propose_action")
        assert not any(k.startswith("calendar_") for k in propose["input_schema"]["properties"]["kind"]["enum"])
        result = turn.run([{"role": "user", "content": "x"}])
        assert "calendar:create_event" in result.notes
        assert [a.kind for a in s.scalars(select(Approval)).all()] == ["calendar_create_event"]
    assert fake.writes() == []


def test_propose_action_cannot_forge_a_calendar_approval(owner, fake):
    from tests.test_loop import Script, tool_use, turn_for

    switch_on(owner, fake)
    forged = {"kind": "calendar_create_event", "summary": "Invite everyone", "rationale": "x"}
    script = Script(tool_use("propose_action", forged), "ok")
    with use_instance(scope_of("s@example.com")), session_scope() as s:
        turn_for(s, script).run([{"role": "user", "content": "x"}])
        assert s.scalars(select(Approval)).all() == []
        assert script.seen[1][-1]["content"][0]["is_error"] is True


def test_connections_shows_which_scopes_google_granted(owner, fake):
    assert owner.get("/app/connections").json()["services"]["google"]["meta"]["scopes"] == [READ]
    switch_on(owner, fake)
    assert set(owner.get("/app/connections").json()["services"]["google"]["meta"]["scopes"]) == {READ, EVENTS}


NOT_ENABLED = {"error": {"code": 403, "message": "Google Calendar API has not been used in project 1 before.",
                         "errors": [{"reason": "accessNotConfigured"}], "status": "PERMISSION_DENIED"}}  # fmt: skip


def test_calendar_api_off_in_the_google_project_is_said_plainly_and_not_retried(owner, fake, caplog):
    import logging

    switch_on(owner, fake)
    fake.fail_with = httpx.Response(403, json=NOT_ENABLED)
    with caplog.at_level(logging.WARNING):
        out, err = events_tool("s@example.com", {"from": "today"})
    assert err
    assert "The Google Calendar API isn't switched on for your Google project." in out
    assert "Enable it under APIs & Services, Library, Google Calendar API, then ask me again." in out
    assert "don't call this tool again" in out
    assert "accessNotConfigured" in caplog.text and "ya29" not in caplog.text
    asked = [c for c in fake.calls if c[0] == "GET" and "/events" in c[1]]
    assert len(asked) == 1  # one call: a 403 like this is not transient


def test_free_busy_and_actions_say_it_too(owner, fake):
    switch_on(owner, fake)
    fake.fail_with = httpx.Response(403, json=NOT_ENABLED)
    for tool, args in [
        ("calendar_free_busy", {"from": "tomorrow"}),
        ("create_event", {"title": "Gym", "when": "tomorrow at 7am"}),
    ]:
        out, err = run("s@example.com", tool, args)
        assert err and "isn't switched on for your Google project" in out, tool


def test_a_missing_permission_is_told_apart_from_the_api_being_off(owner, fake):
    switch_on(owner, fake)
    fake.fail_with = httpx.Response(
        403, json={"error": {"code": 403, "errors": [{"reason": "insufficientPermissions"}]}}
    )
    out, err = events_tool("s@example.com", {"from": "today"})
    assert err and "permission is missing" in out and "Reconnect it under Connections" in out
    assert "isn't switched on" not in out


def test_any_other_error_keeps_the_plain_status_text_and_may_be_retried(owner, fake):
    switch_on(owner, fake)
    fake.fail_with = httpx.Response(500, json={"error": {"status": "INTERNAL"}})
    out, err = events_tool("s@example.com", {"from": "today"})
    assert err and "answered with an error (500" in out and "don't call this tool again" not in out
