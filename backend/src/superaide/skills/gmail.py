"""The Gmail capability's tools, its connection state, and sending an approved reply.

- `gmail_search`, `gmail_read_thread` (read_private): emails come back as `search_result` blocks
  with `ava://mail/<message id>` uris, so they're cited like notes. They mark the turn tainted
  (brain/loop.py): email is written by other people and is never an instruction.
- `gmail_draft_reply` (write_own): a draft in the owner's Gmail, in that thread. The recipients are
  chosen here, in code, from the thread (the last message's sender), never from the model's words,
  so an email can't redirect a reply to someone else.
- `gmail_send` (send): saves the same draft and opens an `Approval` (kind `gmail_send`). Only the
  owner's yes sends it, and what is sent is exactly that draft (`send_approved`).

What reaches the model is limited: sender name and domain (not the address), subject, date and a
short preview from search; each message's own text (no quoted mail, signature or HTML, long
numbers and link tokens blanked) from a thread; nothing at all from mail about passwords or codes.

Everything is per instance: the sign-in is in the instance's vault slot ("google",
"gmail_refresh_token"), the drafts setting on its `installed_skill` row.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.connectors import gmail as api
from superaide.connectors import google
from superaide.db.models import Approval, InstalledSkill, Person

SKILL_ID = "superaide/gmail"
SLOT = ("google", "gmail_refresh_token")
SEARCH_LIMIT = 10
THREAD_LIMIT = 5  # the latest messages of a thread
HIDDEN_RISKS = {"credentials"}  # never shown to the model at all (one-time codes, password resets)
QUIET_RISKS = {"payment", "credentials"}  # no preview in search results
log = logging.getLogger(__name__)


class NotReady(Exception):
    """Gmail can't be used right now; the message says why and what to do."""


# --- connection state ---------------------------------------------------------------------


def _row(session: Session) -> Any:
    from superaide.vault.store import stored

    return stored(session).get(SLOT)


def granted(session: Session) -> set[str]:
    """Scopes the owner granted Gmail, for the OAuth client saved now (a new client means sign in again)."""
    from superaide.vault.store import runtime_settings

    row = _row(session)
    if row is None:
        return set()
    meta = row.meta or {}
    if meta.get("client_id") and meta["client_id"] != runtime_settings().google_client_id:
        return set()
    return set(meta.get("scopes") or [])


def address(session: Session) -> str:
    row = _row(session)
    return str(((row.meta if row is not None else None) or {}).get("address") or "")


def installed(session: Session) -> InstalledSkill | None:
    from superaide.skills.registry import active

    if not any(s.manifest.id == SKILL_ID for s in active(session)):
        return None
    return session.scalar(select(InstalledSkill).where(InstalledSkill.skill_id == SKILL_ID))


def drafts_wanted(session: Session) -> bool:
    row = installed(session)
    return bool(row is not None and (row.settings or {}).get("drafts"))


def can_read(session: Session) -> bool:
    return installed(session) is not None and api.READONLY in granted(session)


def can_draft(session: Session) -> bool:
    return can_read(session) and drafts_wanted(session) and api.COMPOSE in granted(session)


def status(session: Session) -> dict[str, Any]:
    """What the Capabilities card shows. Never a token."""
    from superaide.vault.store import runtime_settings

    s = runtime_settings()
    scopes = granted(session)
    return {
        "installed": installed(session) is not None,
        "client_ready": bool(s.google_client_id and s.google_client_secret),
        "connected": api.READONLY in scopes,
        "address": address(session) if api.READONLY in scopes else "",
        "drafts": drafts_wanted(session),
        "drafts_allowed": api.COMPOSE in scopes,
    }


def token(session: Session) -> str:
    from superaide.vault.store import runtime_settings, unseal

    s = runtime_settings()
    row = _row(session)
    if row is None or not s.google_client_id:
        raise NotReady("Gmail isn't connected yet. Ask the owner to connect it under Capabilities → Gmail.")
    return google.access_token(s.google_client_id, s.google_client_secret, unseal(row), client=api.http_client())


# --- what the model sees ------------------------------------------------------------------


def _when(msg: dict[str, Any]) -> str:
    try:
        return f"{parsedate_to_datetime(api.headers(msg).get('date', '')):%a %d %b %Y, %H:%M}"
    except TypeError, ValueError:
        try:
            return f"{datetime.fromtimestamp(int(msg.get('internalDate', 0)) / 1000, UTC):%a %d %b %Y}"
        except TypeError, ValueError:
            return ""


def who(msg: dict[str, Any]) -> str:
    """The sender as the model sees them: their name and their domain, never the full address."""
    name, addr = api.sender(msg)
    dom = api.domain(addr)
    return f"{name} ({dom})" if name and dom else name or dom or "unknown sender"


def risks(text: str) -> set[str]:
    from superaide.triage.rules import risk_screen

    return set(risk_screen(text))


def _source(msg: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "type": "search_result",
        "source": f"ava://mail/{msg.get('id', '')}",
        "title": f"Email · {who(msg)} · {api.subject(msg)}"[:200],
        "content": [{"type": "text", "text": text}],
        "citations": {"enabled": True},
    }


def search(session: Session, owner: Person, args: dict[str, Any]) -> tuple[Any, bool]:
    try:
        if not can_read(session):
            raise NotReady("Gmail isn't connected yet. Ask the owner to connect it under Capabilities → Gmail.")
        tok = token(session)
        limit = max(1, min(int(args.get("limit") or SEARCH_LIMIT), SEARCH_LIMIT))
        client = api.http_client()
        ids = api.search(client, tok, str(args.get("query") or ""), limit)
        out = []
        for mid in ids:
            msg = api.message(client, tok, mid, fmt="metadata")
            if msg is None:
                continue
            subj = api.subject(msg)
            preview = api.redact(str(msg.get("snippet") or "")[:240])
            if risks(subj + " " + preview) & QUIET_RISKS:
                preview = "(preview withheld: it's about payment details, passwords or codes; open it in Gmail)"
            unread = "UNREAD" in (msg.get("labelIds") or [])
            out.append(
                _source(
                    msg,
                    f"From: {who(msg)}\nSubject: {subj}\nDate: {_when(msg)}{' (unread)' if unread else ''}\n"
                    f"Thread: {msg.get('threadId', '')}\nPreview: {preview}",
                )
            )
    except NotReady as exc:
        return str(exc), True
    except google.GoogleError as exc:
        return str(exc), True
    audit(session, "skills", "gmail_searched", results=len(out))  # never the query or the mail
    if not out:
        return "No emails match that search.", False
    return out, False


def read_thread(session: Session, owner: Person, args: dict[str, Any]) -> tuple[Any, bool]:
    try:
        if not can_read(session):
            raise NotReady("Gmail isn't connected yet. Ask the owner to connect it under Capabilities → Gmail.")
        found = api.thread(api.http_client(), token(session), str(args.get("thread_id") or ""))
    except NotReady as exc:
        return str(exc), True
    except google.GoogleError as exc:
        return str(exc), True
    if found is None:
        return "No such thread. Use a thread id from gmail_search.", True
    out = []
    for msg in (found.get("messages") or [])[-THREAD_LIMIT:]:
        subj = api.subject(msg)
        body = api.clean_body(api.body_text(msg))
        if risks(subj + " " + body) & HIDDEN_RISKS:
            body = "(not shown: it's about a password or a code; the owner can open it in Gmail)"
        out.append(_source(msg, f"From: {who(msg)}\nDate: {_when(msg)}\nSubject: {subj}\n\n{body or '(no text)'}"))
    audit(session, "skills", "gmail_thread_read", messages=len(out))
    return out or "That thread is empty.", not out


# --- replying -------------------------------------------------------------------------------


def _draft(session: Session, args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Save a reply draft in the thread. (draft, the message replied to, recipient). Raises NotReady."""
    if not can_read(session):
        raise NotReady("Gmail isn't connected yet. Ask the owner to connect it under Capabilities → Gmail.")
    if not can_draft(session):
        raise NotReady(
            "Drafting in Gmail is off. Ask the owner to turn on drafts under Capabilities → Gmail "
            "(Google will ask them to allow it)."
        )
    text = str(args.get("text") or "").strip()
    if not text:
        raise NotReady("The reply is empty.")
    client, tok = api.http_client(), token(session)
    found = api.thread(client, tok, str(args.get("thread_id") or ""))
    if found is None:
        raise NotReady("No such thread. Use a thread id from gmail_search.")
    mine = address(session)
    theirs = [m for m in found.get("messages") or [] if api.sender(m)[1] and api.sender(m)[1] != mine]
    if not theirs:
        raise NotReady("There's no one to reply to in that thread.")
    original = theirs[-1]
    to = api.sender(original)[1]  # chosen here, from the thread: never an address the model wrote
    raw = api.reply_raw(original=original, to=[to], from_addr=mine, text=text[:5000])
    draft = api.create_draft(client, tok, raw, str(found.get("id") or original.get("threadId") or ""))
    return draft, original, to


def draft_reply(session: Session, owner: Person, args: dict[str, Any]) -> tuple[str, bool]:
    try:
        draft, original, to = _draft(session, args)
    except NotReady as exc:
        return str(exc), True
    except google.GoogleError as exc:
        return str(exc), True
    audit(session, "skills", "gmail_draft_saved", draft_id=str(draft.get("id", "")))
    name = api.sender(original)[0] or api.domain(to)
    return (
        f"Draft saved in the owner's Gmail, replying to {name} in “{api.subject(original)}”. "
        "Nothing was sent; they can edit or send it from Gmail, or ask you to send it."
    ), False


def send(session: Session, owner: Person, args: dict[str, Any], *, tainted: bool) -> tuple[str, bool]:
    """Never sends: saves the draft and asks the owner to approve sending exactly that draft."""
    from superaide.triage.store import current_stage

    try:
        draft, original, to = _draft(session, args)
    except NotReady as exc:
        return str(exc), True
    except google.GoogleError as exc:
        return str(exc), True
    text = str(args.get("text") or "").strip()
    name = api.sender(original)[0]
    approval = Approval(
        kind="gmail_send",
        payload={
            "summary": f"Email {name + ' ' if name else ''}<{to}>, “{api.subject(original)}”: {text[:400]}",
            "draft_id": str(draft.get("id", "")),
            "thread_id": str(original.get("threadId", "")),
            "to": [to],
            "tainted": tainted,  # email (or the web) was read in the turn that asked for this
            "stage": str(current_stage(session)),
        },
        requested_by_person_id=owner.id,
    )
    session.add(approval)
    session.flush()
    audit(session, "policy", "approval_requested", kind="gmail_send", approval_id=approval.id, tainted=tainted)
    return (
        "Saved as a draft and waiting for the owner's approval on their dashboard; it is sent only when they "
        "approve. Tell them it's waiting."
    ), False


def send_approved(session: Session, approval: Approval) -> str:
    """The executor for an approved `gmail_send`: send exactly the draft the owner saw."""
    draft_id = str((approval.payload or {}).get("draft_id") or "")
    if not draft_id:
        return "Approved, but there was no draft to send."
    if not can_draft(session):
        return "Approved, but Gmail can't send right now: reconnect Gmail with drafts on, then send it from Gmail."
    try:
        sent = api.send_draft(api.http_client(), token(session), draft_id)
    except (NotReady, google.GoogleError) as exc:
        log.warning("couldn't send an approved Gmail draft: %s", exc)
        return f"Approved, but it couldn't be sent: {exc} The draft is still in Gmail."
    audit(session, "skills", "gmail_sent", approval_id=approval.id, message_id=str(sent.get("id", "")))
    return "Sent from your Gmail."


def run(session: Session, owner: Person, name: str, args: dict[str, Any], *, tainted: bool) -> tuple[Any, bool]:
    """One Gmail tool call from the owner's own turn. Refused for anyone else, and when the
    capability isn't installed (a model can name a tool it wasn't offered)."""
    from superaide.app.accounts import owner as instance_owner

    me = instance_owner(session)
    if me is None or me.id != owner.id:
        audit(session, "skills", "gmail_refused", reason="not the owner", tool=name)
        return "Only the owner's own chat can use Gmail.", True
    if installed(session) is None:
        return "The Gmail capability isn't installed. The owner can add it under Capabilities.", True
    if name == "gmail_search":
        return search(session, owner, args)
    if name == "gmail_read_thread":
        return read_thread(session, owner, args)
    if name == "gmail_draft_reply":
        return draft_reply(session, owner, args)
    if name == "gmail_send":
        return send(session, owner, args, tainted=tainted)
    return f"Unknown tool {name!r}.", True


TOOLS = {"gmail_search", "gmail_read_thread", "gmail_draft_reply", "gmail_send"}
READS = {"gmail_search", "gmail_read_thread"}
