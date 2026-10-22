"""/app/intro/voice: the introduction's voice call, v2 (ADR 0012). 404 while VOICE_INTRO_V2 is off.

Owner only, like the rest of /app: the session cookie, the X-Requested-With header on every
POST (CSRF), and the instance the owner hired in scope (ADR 0009). One utterance per request:
the browser's voice activity detection decides when the owner has finished, then posts the
audio (or the text its own speech recognition heard, or what they typed); the reply streams
back as Server-Sent Events. A plain request, not a WebSocket, so it passes through the web
app's same-origin /api proxy with the session cookie (ADR 0005).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from superaide.app.routes import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.config import get_settings
from superaide.db.models import Person
from superaide.db.session import require, session_scope
from superaide.voice.call import Turn, VoiceCall, stream

MAX_AUDIO_BYTES = 2_500_000  # about a minute of 16 kHz, 16-bit mono WAV
AUDIO_TYPES = ("audio/wav", "audio/x-wav", "audio/wave", "audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4")
LANG = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}")
STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class StyleBody(BaseModel):
    voice: str = Field(max_length=30)


class StartBody(BaseModel):
    lang: str = Field(default="en-US", max_length=20)


class FactBody(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    category: str = Field(default="about", max_length=20)


class ConfirmBody(BaseModel):
    facts: list[FactBody] = Field(default_factory=list, max_length=40)


def _lang(value: str) -> str:
    return value if LANG.fullmatch(value or "") else "en-US"


def owner_on_call(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
    """The owner (401/403 first, like every owner route), then 404 while the call is switched off."""
    if not get_settings().voice_intro_v2:
        raise HTTPException(404, "Not Found")
    return person_id


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/intro/voice",
        tags=["Introduction"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def voice() -> VoiceCall:
        return VoiceCall(get_agent())

    @router.get(
        "",
        summary="Voice call settings",
        description="Which speech-to-text and voice the call will use, who pays for them, and the voice choices.",
    )
    def config(lang: str = "en-US", person_id: uuid.UUID = Depends(owner_on_call)) -> dict[str, Any]:
        with session_scope() as s:
            return voice().config(s, require(s, Person, person_id), _lang(lang))

    @router.post(
        "/settings", summary="Choose Ava's voice", description="`voice`: formal_female, formal_male or neutral."
    )
    def settings(body: StyleBody, person_id: uuid.UUID = Depends(owner_on_call)) -> dict[str, Any]:
        with session_scope() as s:
            person = require(s, Person, person_id)
            try:
                voice().set_style(s, person, body.voice)
            except ValueError as exc:
                raise HTTPException(422, "Choose formal_female, formal_male or neutral.") from exc
            return voice().config(s, person, "en-US")

    @router.post(
        "/start",
        summary="Start the voice call",
        description="Starts a fresh call. Streams Ava's greeting as `sentence` and `audio` events, then `done`.",
        response_class=StreamingResponse,
    )
    def start(body: StartBody | None = None, person_id: uuid.UUID = Depends(owner_on_call)) -> StreamingResponse:
        lang = _lang(body.lang if body else "en-US")
        return StreamingResponse(
            stream(voice().start(person_id, lang)), media_type="text/event-stream", headers=STREAM_HEADERS
        )

    @router.post(
        "/turn",
        summary="Say something on the call",
        description=(
            "One utterance, as multipart form data: `audio` (what the microphone heard, transcribed on "
            "the server) or `text` (what the browser's own speech recognition heard, or what was typed); "
            "`interrupted` and `heard` when the owner talked over Ava (the part of her reply they heard); "
            "`lang`. Streams `transcript`, `sentence`, `audio` and `done` events. Audio is never stored."
        ),
        response_class=StreamingResponse,
    )
    async def turn(
        audio: UploadFile | None = File(default=None),
        text: str = Form(default="", max_length=4000),
        heard: str | None = Form(default=None, max_length=4000),
        interrupted: bool = Form(default=False),
        lang: str = Form(default="en-US", max_length=20),
        person_id: uuid.UUID = Depends(owner_on_call),
    ) -> StreamingResponse:
        data: bytes | None = None
        mime = "audio/wav"
        if audio is not None:
            mime = (audio.content_type or "audio/wav").split(";")[0].strip().lower()
            if mime not in AUDIO_TYPES:
                raise HTTPException(415, "Send the recording as WAV, WebM, Ogg, MP3 or MP4 audio.")
            data = await audio.read(MAX_AUDIO_BYTES + 1)
            if len(data) > MAX_AUDIO_BYTES:
                raise HTTPException(413, "That answer is too long. Try saying it in a few shorter parts.")
            if not data:
                data = None
        if data is None and not text.strip():
            raise HTTPException(422, "Say or type something first.")
        clean = text.replace("\x00", "")
        t = Turn(
            audio=data,
            mime=mime,
            text=clean,
            heard=(heard or "").replace("\x00", "") if (interrupted or heard) else None,
            lang=_lang(lang),
        )
        return StreamingResponse(
            stream(voice().turn(person_id, t)), media_type="text/event-stream", headers=STREAM_HEADERS
        )

    @router.post(
        "/finish",
        summary="End the call",
        description="Ends the call and proposes what Ava learned. Nothing is saved until you confirm.",
    )
    def finish(person_id: uuid.UUID = Depends(owner_on_call)) -> dict[str, Any]:
        with session_scope() as s:
            return voice().finish(s, require(s, Person, person_id))

    @router.post(
        "/confirm",
        summary="Save what Ava learned",
        description="Saves the facts you checked and edited (private to you). An empty list saves nothing.",
    )
    def confirm(body: ConfirmBody, person_id: uuid.UUID = Depends(owner_on_call)) -> dict[str, Any]:
        with session_scope() as s:
            return voice().confirm(s, require(s, Person, person_id), [f.model_dump() for f in body.facts])

    return router
