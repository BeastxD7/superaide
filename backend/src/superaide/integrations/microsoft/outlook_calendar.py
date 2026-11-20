"""The Outlook calendar tools: list events, check when the owner is free, propose a new event. Issue #668.

Reads are for the owner's own direct chat, audited by count, rate-limited per hour, limited to a window from today and
cut to the owner's settings. Event titles are written by other people (invitations): quoted data that taints the turn
(brain/loop.py), never instructions. A new event goes through `policy.actions.decide` as an approval whose payload is
exactly what will be created; it is created on the owner's own calendar with no attendees, so nobody is invited.
Nothing here edits, moves, declines or deletes an event.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.identity.models import Person
from superaide.integrations.microsoft import calendar_settings as cal_settings
from superaide.integrations.microsoft import client as api
from superaide.integrations.microsoft import mail
from superaide.platform.audit import audit
from superaide.platform.db.models import AuditLog
from superaide.policy.actions import EXECUTORS, TOOL_RULES, decide
from superaide.skills.models import InstalledSkill

SKILL_ID = cal_settings.SKILL_ID
SLOT = ("microsoft", "calendar_refresh_token")
READ_EVENTS = ("m365_calendar_read", "m365_calendar_free_busy")
NOT_CONNECTED = (
    "Outlook calendar isn't connected yet. Ask the owner to connect it under Capabilities → Outlook calendar."
)
MIN_MINUTES, MAX_MINUTES = 5, 24 * 60
BUSY = {"busy", "oof", "tentative", "workingElsewhere"}


class NotReady(Exception):
    """The calendar can't be used right now; the message says why and what to do."""


# --- connection state ---------------------------------------------------------------------


def _row(session: Session) -> Any:
    from superaide.identity.vault.store import stored

    return stored(session).get(SLOT)


def installed(session: Session) -> InstalledSkill | None:
    from superaide.skills.registry import active

    if not any(s.manifest.id == SKILL_ID for s in active(session)):
        return None
    return session.scalar(select(InstalledSkill).where(InstalledSkill.skill_id == SKILL_ID))


def granted(session: Session) -> set[str]:
    row = _row(session)
    return {str(x) for x in (row.meta or {}).get("scopes", [])} if row is not None else set()


def changes_wanted(session: Session) -> bool:
    row = installed(session)
    return bool(row is not None and (row.settings or {}).get("changes"))


def connected(session: Session) -> bool:
    return _row(session) is not None


def can_read(session: Session) -> bool:
    return installed(session) is not None and connected(session)


def can_write(session: Session) -> bool:
    return can_read(session) and changes_wanted(session) and api.CAL_WRITE_SCOPE in granted(session)


def status(session: Session) -> dict[str, Any]:
    row = _row(session)
    return {
        "installed": installed(session) is not None,
        "client_saved": mail.client_saved(session),
        "connected": row is not None,
        "address": str((row.meta or {}).get("address") or "") if row is not None else "",
        "hint": row.hint if row is not None else "",
        "changes": changes_wanted(session),
        "changes_granted": api.CAL_WRITE_SCOPE in granted(session),
    }


def token(session: Session) -> str:
    """A fresh access token; Microsoft rotates the refresh token on use, so the new one replaces the old."""
    from superaide.identity.vault.crypto import mask
    from superaide.identity.vault.store import seal, unseal

    row = _row(session)
    if row is None:
        raise NotReady(NOT_CONNECTED)
    try:
        cid, secret = mail.credentials(session)
    except mail.NotReady as exc:
        raise NotReady(str(exc)) from exc
    scopes = [*api.CAL_READ_SCOPES, *([api.CAL_WRITE_SCOPE] if api.CAL_WRITE_SCOPE in granted(session) else [])]
    old = unseal(row)
    access, new = api.refresh(
        client_id=cid, client_secret=secret, refresh_token=old, scopes=scopes, client=api.http_client()
    )
    if new != old:
        seal(row, new)
        row.hint = mask(new)
        row.updated_at = datetime.now(UTC)
    return access


def _rate_limited(session: Session, cfg: dict[str, Any]) -> str | None:
    since = datetime.now(UTC) - timedelta(hours=1)
    used = int(
        session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action.in_(READ_EVENTS), AuditLog.created_at > since)
        )
        or 0
    )
    if used >= int(cfg["reads_per_hour"]):
        audit(session, "skills", "m365_calendar_rate_limited", limit=cfg["reads_per_hour"])
        return (
            f"Calendar lookups are paused for now: the hourly limit was reached. {cal_settings.note('reads_per_hour')} "
            "Tell the owner and try again later."
        )
    return None


# --- the window ---------------------------------------------------------------------------


def _window(cfg: dict[str, Any], args: dict[str, Any]) -> tuple[datetime, datetime]:
    """[start, end) in UTC from `date` (YYYY-MM-DD, default today) and `days`, never beyond the owner's window."""
    today = datetime.now(UTC).date()
    try:
        first = date.fromisoformat(str(args.get("date") or today.isoformat()))
        days = int(args.get("days") or 1)
    except ValueError as exc:
        raise api.MicrosoftError("date must be YYYY-MM-DD and days a number.") from exc
    last = today + timedelta(days=int(cfg["window_days"]))
    if first < today - timedelta(days=1) or first > last:
        raise api.MicrosoftError(f"That day is outside what the owner allows. {cal_settings.note('window_days')}")
    days = max(1, min(days, int(cfg["window_days"])))
    end = min(first + timedelta(days=days), last + timedelta(days=1))
    return datetime.combine(first, time.min, UTC), datetime.combine(end, time.min, UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _parse(value: Any) -> datetime:
    raw = str((value or {}).get("dateTime") or "")[:19]
    return datetime.fromisoformat(raw).replace(tzinfo=UTC)


# --- what the model sees ------------------------------------------------------------------


def _source(ev: dict[str, Any], text: str, title: str) -> dict[str, Any]:
    return {
        "type": "search_result",
        "source": f"ava://outlook/event/{str(ev.get('id', ''))[:40].replace('/', '_')}",
        "title": f"Outlook calendar · {title}"[:200],
        "content": [{"type": "text", "text": text}],
        "citations": {"enabled": True},
    }


def calendar_events(session: Session, owner: Person, args: dict[str, Any]) -> tuple[Any, bool]:
    cfg = cal_settings.load(session)
    try:
        if not can_read(session):
            raise NotReady(NOT_CONNECTED)
        if limited := _rate_limited(session, cfg):
            return limited, True
        start, end = _window(cfg, args)
        events = api.calendar_view(api.http_client(), token(session), _iso(start), _iso(end), int(cfg["events_limit"]))
    except (NotReady, api.MicrosoftError) as exc:
        return str(exc), True
    out = []
    for ev in events[: int(cfg["events_limit"])]:
        title = api.clean_text(str(ev.get("subject") or ""), int(cfg["title_chars"])).replace("\n", " ") or "(no title)"
        where = api.clean_text(str((ev.get("location") or {}).get("displayName") or ""), 80).replace("\n", " ")
        organizer = api.clean_text(str(((ev.get("organizer") or {}).get("emailAddress") or {}).get("name") or ""), 60)
        when = (
            "all day"
            if ev.get("isAllDay")
            else f"{_parse(ev.get('start')):%Y-%m-%d %H:%M}-{_parse(ev.get('end')):%H:%M} UTC"
        )
        text = f"{title}\nWhen: {when} ({ev.get('showAs', 'busy')})" + (f"\nWhere: {where}" if where else "")
        text += f"\nOrganizer: {organizer}" if organizer else ""
        out.append(_source(ev, text, f"{title} · {when}"))
    audit(session, "skills", "m365_calendar_read", events=len(out))  # never a title
    return (out, False) if out else ("Nothing on the calendar in that window.", False)


def calendar_free_busy(session: Session, owner: Person, args: dict[str, Any]) -> tuple[Any, bool]:
    cfg = cal_settings.load(session)
    try:
        if not can_read(session):
            raise NotReady(NOT_CONNECTED)
        if limited := _rate_limited(session, cfg):
            return limited, True
        start, end = _window(cfg, args)
        events = api.calendar_view(api.http_client(), token(session), _iso(start), _iso(end), 50)
    except (NotReady, api.MicrosoftError) as exc:
        return str(exc), True
    busy = sorted((_parse(e["start"]), _parse(e["end"])) for e in events if str(e.get("showAs", "busy")) in BUSY)
    merged: list[list[datetime]] = []
    for s, e in busy:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    free, cursor = [], start
    for s, e in merged:
        if s > cursor:
            free.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < end:
        free.append((cursor, end))
    audit(session, "skills", "m365_calendar_free_busy", busy=len(merged))
    lines = [f"Busy {s:%Y-%m-%d %H:%M}-{e:%H:%M} UTC" for s, e in merged] or ["No busy time."]
    lines += [f"Free {s:%Y-%m-%d %H:%M}-{e:%Y-%m-%d %H:%M} UTC" for s, e in free[:20]]
    return "\n".join(lines) + "\n(Times are UTC. Titles and details are not included.)", False


# --- a new event: always the owner's approval ----------------------------------------------


def _guard(session: Session, sender: Any) -> str | None:
    if sender.relation != "manager":
        return "Only the owner can have events added to their Outlook calendar."
    if installed(session) is None:
        return "The Outlook calendar capability isn't installed. The owner can add it under Capabilities."
    if not connected(session):
        return NOT_CONNECTED
    if not changes_wanted(session):
        return f"Outlook calendar changes are off. The owner can allow them under {cal_settings.CARD_NAME}."
    if api.CAL_WRITE_SCOPE not in granted(session):
        return f"Microsoft hasn't allowed calendar changes yet. The owner can finish it under {cal_settings.CARD_NAME}."
    return None


def calendar_create_event(session: Session, sender: Any, args: dict[str, Any], *, tainted: bool) -> tuple[str, bool]:
    if problem := _guard(session, sender):
        return problem, True
    title = " ".join(api.clean_text(str(args.get("title") or ""), 400).split())[:200]
    try:
        start = datetime.fromisoformat(str(args.get("start") or ""))
        minutes = int(args.get("minutes") or 60)
    except ValueError:
        return "start must be an ISO time with its offset (2026-11-25T15:00:00+05:30) and minutes a number.", True
    if start.tzinfo is None:
        return "start needs a timezone offset, e.g. 2026-11-25T15:00:00+05:30.", True
    if not title:
        return "Give the event a title.", True
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        return f"An event lasts between {MIN_MINUTES} minutes and 24 hours.", True
    start_utc = start.astimezone(UTC)
    if start_utc < datetime.now(UTC) - timedelta(minutes=5):
        return "That time is in the past.", True
    end_utc = start_utc + timedelta(minutes=minutes)
    note = " Proposed after Ava read other people's text this turn: check it." if tainted else ""
    summary = (
        f"Add “{title}” to your Outlook calendar, {start_utc:%Y-%m-%d %H:%M}-{end_utc:%H:%M} UTC, no invitations.{note}"
    )
    payload = {"title": title, "start": _iso(start_utc), "end": _iso(end_utc), "tainted": tainted}
    decision = decide(
        session, kind="m365_calendar_create_event", summary=summary, requester=sender, rules=TOOL_RULES,
        payload=payload, tainted=tainted,
    )  # fmt: skip
    if decision.outcome == "approval":
        return (
            f"Waiting for the owner's approval: {summary} Tell them it's waiting for their OK; it isn't done yet.",
            False,
        )
    return f"Refused: {decision.reason}.", True


def _carry_out(session: Session, approval: Any) -> str:
    p = dict(approval.payload or {})
    try:
        if not can_write(session):
            return "Approved, but the Outlook calendar isn't connected with changes on any more. Nothing was added."
        api.create_event(api.http_client(), token(session), str(p["title"]), str(p["start"]), str(p["end"]))
    except NotReady as exc:
        return f"Approved, but {exc} Nothing was added."
    except api.MicrosoftError as exc:
        return f"Approved, but Microsoft said: {exc} Nothing was added."
    audit(session, "skills", "m365_calendar_create_event", approval_id=approval.id)
    return f"Added “{p['title']}” to your Outlook calendar."


EXECUTORS["m365_calendar_create_event"] = _carry_out

TOOLS = {"m365_calendar_events", "m365_calendar_free_busy", "m365_calendar_create_event"}
READS = {"m365_calendar_events", "m365_calendar_free_busy"}


def run(session: Session, sender: Any, name: str, args: dict[str, Any], *, tainted: bool) -> tuple[Any, bool]:
    from superaide.app.accounts import owner as instance_owner

    me = instance_owner(session)
    person = getattr(sender, "person", None)
    if me is None or person is None or me.id != person.id or sender.relation != "manager":
        audit(session, "skills", "m365_calendar_refused", reason="not the owner", tool=name)
        return "Only the owner's own chat can use the Outlook calendar.", True
    if installed(session) is None:
        return "The Outlook calendar capability isn't installed. The owner can add it under Capabilities.", True
    if name == "m365_calendar_events":
        return calendar_events(session, person, args)
    if name == "m365_calendar_free_busy":
        return calendar_free_busy(session, person, args)
    if name == "m365_calendar_create_event":
        return calendar_create_event(session, sender, args, tainted=tainted)
    return f"Unknown tool {name!r}.", True
