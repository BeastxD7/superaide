"""A voice note or photo from someone Ava does not know (#501, #511), in receptionist mode.

Only when the owner chose a voice-note or photo service, and only behind the same gates as a receptionist turn
(receptionist on, a direct message, not over the hourly or daily message limit, not over the storage cap), plus a small
number of files per sender per day. Everything else stays as before: the fixed reply, nothing downloaded.

Code decides everything here and no model reads what comes out: the transcript or description is untrusted text from
a stranger's file. It is stored as data for the owner (the same row a typed message makes, labelled as software's
reading of a file), the safety signals run over it like over typed text, and it is never used to link an account,
claim a name, set a reminder or run an action. The visitor gets one fixed sentence.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.channels import api as attachments
from superaide.channels.base import InboundMessage
from superaide.identity.resolver import Sender
from superaide.memory.models import Context, Event
from superaide.platform.audit import audit
from superaide.platform.db.models import AuditLog
from superaide.reception import ask, signals
from superaide.reception import turn as rturn

log = logging.getLogger(__name__)

PER_DAY = 5  # files read per sender per day
MAX_AUDIO_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
LABELS = {
    "audio": "Voice note from someone I don't know, transcribed by software",
    "image": "Photo from someone I don't know, described by software",
}
REPLY = "I got your {what} and passed it on to {who}."


def handle(
    agent: Any, session: Session, owner: Any, sender: Sender, ctx: Context, msg: InboundMessage, inbound: Event
) -> str | None:
    """The reply, or None for the old fixed reply (not configured, a gate said no, the file was refused)."""
    from superaide.reception import service as reception

    media = attachments.media_for(agent.settings)
    if media is None or not msg.file_refs or not (media.configured("vision") or media.configured("transcribe")):
        return None
    now = datetime.now(UTC)
    rs = reception.settings_for(session, owner)
    hour, day = reception._counts(session, sender, now)
    if (
        hour > rs.per_hour
        or day > rs.per_day
        or reception._stored_today(session, sender, now) >= reception.STORE_PER_DAY
    ):
        return None
    since = now - timedelta(hours=24)
    used = session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == "visitor_media_read",
            AuditLog.created_at > since,
            AuditLog.detail["identity_id"].astext == str(sender.identity.id),
        )
    )
    if (used or 0) >= PER_DAY:
        return None
    ref = msg.file_refs[0]
    fetch = getattr(agent.messengers.get(msg.provider), "fetch_file", None)
    if fetch is None:
        return None
    try:
        data = fetch(ref, MAX_IMAGE_BYTES)
    except Exception as exc:  # FileTooBig, FileFetchError, anything else: the fixed reply, never an error to a stranger
        log.info("visitor media: not fetched (%s)", type(exc).__name__)
        return None
    kind = attachments.sniff(data)
    audit(session, "reception", "visitor_media_read", identity_id=str(sender.identity.id), kind=kind)
    try:
        if kind == "audio":
            text = media.transcribe(data, purpose="visitor_stt", max_bytes=MAX_AUDIO_BYTES)
        elif kind == "image":
            text = media.describe(data, purpose="visitor_vision", max_bytes=MAX_IMAGE_BYTES)
        else:
            return None  # a document or anything else from a stranger is never read
    except attachments.MediaError as exc:
        audit(session, "reception", "visitor_media_refused", identity_id=str(sender.identity.id), reason=str(exc)[:120])
        return None
    text = text.strip()
    if not text:
        return None
    body = f"[{LABELS[kind]}] {text}"[:2000]
    row = reception._keep(
        agent, session, owner, sender, ctx, msg, inbound, rturn.TakenMessage(message=body), source="code",
        sig=signals.detect(text),
    )  # fmt: skip
    if row is None:
        return reception.SAVE_FAILED
    return REPLY.format(what="voice note" if kind == "audio" else "photo", who=ask.owner_ref(session, owner))
