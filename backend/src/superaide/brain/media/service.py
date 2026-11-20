"""The owner's choice per capability, and the one metered path every media call takes.

Settings live in `model_role` rows with the roles "transcribe" and "vision" (provider = an endpoint connected on the AI
model page, or "anthropic" for vision; the model is whatever that endpoint offers), so there is no table of its own and
nothing is sent until the owner picks. Credentials are the endpoint's own (vault), never copied.

Every call reserves its worst case against the spend cap first and settles from what the service reports
(`brain/spend/budget.py`): a refused cap, an unpriced model or a missing key ends in `MediaRefused` with a sentence for
the owner, and the file is never sent. Nothing here stores audio or images.
"""

from __future__ import annotations

import io
import logging
import struct
import wave
import zlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.brain.media import prices as mp
from superaide.brain.media.providers import (
    DESCRIBE_PROMPT,
    FakeTranscriber,
    FakeVision,
    MediaError,
)
from superaide.brain.models import ModelRole, OwnerModelPrice
from superaide.brain.spend.pricing import UnknownModelPrice, UsageCounts

log = logging.getLogger(__name__)

CAPABILITIES = ("transcribe", "vision")
LABELS = {"transcribe": "Voice notes (speech to text)", "vision": "Photos and images"}
FAKE_KEY = "fake"  # offline responder, offered only while FAKE_LLM is on (tests, demos)
SHARED_FAKES: dict[str, Any] = {"vision": FakeVision(), "transcribe": FakeTranscriber()}


class MediaRefused(MediaError):
    """The call was not made, or failed; the message is a sentence safe to show."""


@dataclass(frozen=True)
class Choice:
    provider: str
    model: str


# --- the owner's choice -------------------------------------------------------------------------------------------


OFF = "off"  # the stored "turned off on purpose" marker; it is what stops the photo default from coming back
DEFAULT_VISION = Choice("anthropic", "claude-haiku-5-5")


def get_choices(session: Session) -> dict[str, Choice]:
    """What the owner saved. A "turned off" marker is not a choice, so it is left out."""
    from superaide.platform.tenancy.context import mine

    rows = session.scalars(select(ModelRole).where(mine(ModelRole), ModelRole.role.in_(CAPABILITIES)))
    return {r.role: Choice(r.provider, r.model) for r in rows if r.provider != OFF}


def _turned_off(session: Session) -> set[str]:
    from superaide.platform.tenancy.context import mine

    rows = session.scalars(select(ModelRole).where(mine(ModelRole), ModelRole.role.in_(CAPABILITIES)))
    return {r.role for r in rows if r.provider == OFF}


def effective_choices(session: Session, settings: Any) -> tuple[dict[str, Choice], set[str]]:
    """The choices in force, and which of them are defaults the owner never picked.

    Photos default to Claude when the owner has connected Claude and has neither chosen a photo service nor turned
    photos off (the owner's rule: a feature that needs a key turns on once the key exists). Voice has no default:
    there is no audio vendor to assume, so it stays off until a transcriber is configured.
    """
    chosen = get_choices(session)
    defaults: set[str] = set()
    if "vision" not in chosen and "vision" not in _turned_off(session):
        if provider_ready(settings, "vision", DEFAULT_VISION.provider) is None:
            chosen["vision"] = DEFAULT_VISION
            defaults.add("vision")
    return chosen, defaults


def set_choice(session: Session, cap: str, provider: str, model: str, *, user_id: Any) -> None:
    from datetime import UTC, datetime

    from superaide.platform.audit import audit
    from superaide.platform.tenancy.context import mine

    if cap not in CAPABILITIES:
        raise ValueError(f"unknown capability {cap!r}")
    row = session.scalar(select(ModelRole).where(mine(ModelRole), ModelRole.role == cap))
    if row is None:
        row = ModelRole(role=cap)
        session.add(row)
    row.provider, row.model, row.set_by_user_id, row.updated_at = provider, model, user_id, datetime.now(UTC)
    session.flush()
    audit(session, "providers", "media_provider_set", capability=cap, provider=provider, user_id=user_id)


def clear_choice(session: Session, cap: str, *, user_id: Any) -> None:
    from superaide.platform.audit import audit
    from superaide.platform.tenancy.context import mine

    for row in session.scalars(select(ModelRole).where(mine(ModelRole), ModelRole.role == cap)):
        session.delete(row)
    if cap == "vision":  # remember it was turned off, or the Claude default would switch it back on
        session.flush()
        set_choice(session, cap, OFF, OFF, user_id=user_id)
    audit(session, "providers", "media_provider_cleared", capability=cap, user_id=user_id)


def owner_stt_prices(session: Session) -> dict[tuple[str, str], Decimal]:
    from superaide.platform.tenancy.context import mine

    out: dict[tuple[str, str], Decimal] = {}
    for r in session.scalars(select(OwnerModelPrice).where(mine(OwnerModelPrice))):
        if r.provider.startswith("stt:"):
            out[(r.provider[4:], r.model)] = r.input_usd_per_mtok
    return out


def set_stt_price(session: Session, key: str, model: str, per_minute: Decimal, *, user_id: Any) -> None:
    from superaide.brain.spend.pricing import set_owner_price

    set_owner_price(
        session, provider=mp.stt_owner_provider(key), model=model, input_usd_per_mtok=per_minute,
        output_usd_per_mtok=Decimal(0), user_id=user_id,
    )  # fmt: skip


# --- file types, from the first bytes only ------------------------------------------------------------------------


def sniff_audio(data: bytes) -> tuple[str, str] | None:
    """(file name we choose, MIME type) for a real audio container, else None. The sender's name is never used."""
    if data.startswith(b"OggS"):
        return "voice.ogg", "audio/ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "voice.wav", "audio/wav"
    if data.startswith(b"fLaC"):
        return "voice.flac", "audio/flac"
    if data.startswith(b"ID3") or (
        len(data) > 2 and data[0] == 0xFF and data[1] & 0xE6 == 0xE2 and (data[1] >> 3) & 3 != 1
    ):  # MPEG layer III
        return "voice.mp3", "audio/mpeg"
    if data[4:8] == b"ftyp":
        return "voice.m4a", "audio/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "voice.webm", "audio/webm"
    return None


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def sample_png() -> bytes:
    """A valid 8x8 grey PNG, for "test connection"."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    rows = b"".join(b"\x00" + b"\x80" * 8 for _ in range(8))
    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def sample_wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(b"\x00\x00" * 8_000)  # half a second of silence
    return buf.getvalue()


# --- building an adapter ------------------------------------------------------------------------------------------

ADAPTER_FACTORY: Any = None  # tests replace it: (cap, provider key, settings) -> adapter | None


def provider_ready(settings: Any, cap: str, key: str) -> str | None:
    """None when this endpoint can serve the capability, else why not (a sentence for the owner)."""
    from superaide.brain.providers.presets import PRESETS
    from superaide.brain.wiring import provider_usable

    if key == FAKE_KEY:
        return None if settings.fake_llm else "The offline test responder is only available while FAKE_LLM is on."
    if key == "anthropic":
        if cap != "vision":
            return "Claude does not transcribe audio. Choose an OpenAI-compatible service for voice notes."
        return None if provider_usable(settings, key) else "Connect Claude first (AI model page)."
    cfg = settings.llm_providers.get(key)
    if cfg is None or cfg.get("preset", key) not in PRESETS or not provider_usable(settings, key):
        return "Connect that provider first on the AI model page."
    return None


def build_adapter(settings: Any, cap: str, key: str) -> Any:  # an ImageDescriber or a Transcriber
    if ADAPTER_FACTORY is not None and (made := ADAPTER_FACTORY(cap, key, settings)) is not None:
        return made
    why = provider_ready(settings, cap, key)
    if why:
        raise MediaRefused(why)
    if key == FAKE_KEY:
        return SHARED_FAKES[cap]
    from superaide.brain.media.providers import AnthropicVision, OpenAICompatTranscriber, OpenAICompatVision
    from superaide.brain.providers.presets import PRESETS

    if key == "anthropic":
        return AnthropicVision(settings.anthropic_api_key)
    cfg = settings.llm_providers[key]
    preset = PRESETS[cfg.get("preset", key)]
    common: dict[str, Any] = {
        "label": preset.label, "azure": preset.azure_key_header, "local": preset.local,
    }  # fmt: skip
    if cap == "vision":
        return OpenAICompatVision(
            cfg.get("base_url", ""), cfg.get("api_key", ""), max_tokens_param=preset.max_tokens_param, **common
        )
    return OpenAICompatTranscriber(cfg.get("base_url", ""), cfg.get("api_key", ""), **common)


def kind_of(settings: Any, key: str) -> str:
    return "anthropic" if key == "anthropic" else str((settings.llm_providers.get(key) or {}).get("preset", key))


# --- the metered service ------------------------------------------------------------------------------------------


class MediaService:
    """What the pipeline and the settings page call. One per message: it reads the owner's choice once."""

    def __init__(self, settings: Any, choices: dict[str, Choice] | None = None) -> None:
        self.settings = settings
        self.choices = choices if choices is not None else self._load()

    def _load(self) -> dict[str, Choice]:
        from superaide.platform.db.session import session_scope

        with session_scope() as s:
            return effective_choices(s, self.settings)[0]

    def configured(self, cap: str) -> bool:
        c = self.choices.get(cap)
        return c is not None and provider_ready(self.settings, cap, c.provider) is None

    def _guard(self, prices: Any) -> Any:
        from superaide.brain.spend.budget import BudgetGuard

        return BudgetGuard.for_instance(self.settings, prices=prices)

    def _refuse(self, exc: Exception) -> MediaRefused:
        from superaide.brain.spend.budget import AgentFrozen, BudgetExceeded

        if isinstance(exc, BudgetExceeded):
            return MediaRefused(f"I can't read that right now: {exc}")
        if isinstance(exc, AgentFrozen):
            return MediaRefused("I'm paused, so I didn't read that.")
        if isinstance(exc, UnknownModelPrice):
            return MediaRefused("The model chosen for this has no price yet. Set one on the AI model page.")
        return MediaRefused(str(exc) if isinstance(exc, MediaError) else "I couldn't read that file.")

    def transcribe(self, audio: bytes, *, purpose: str = "media_stt", max_bytes: int = mp.MAX_AUDIO_BYTES) -> str:
        """The words in a voice note. Raises MediaRefused (a sentence) and never sends an over-limit or unknown file."""
        choice = self.choices.get("transcribe")
        if choice is None or not self.configured("transcribe"):
            raise MediaRefused("I can't listen to voice notes yet.")
        kind = sniff_audio(audio)
        if kind is None:
            raise MediaRefused("I can only listen to voice notes in a common audio format.")
        if len(audio) > max_bytes:
            raise MediaRefused("That voice note is too long for me to listen to.")
        from superaide.brain.wiring import load_prices
        from superaide.platform.db.session import session_scope

        try:
            adapter = build_adapter(self.settings, "transcribe", choice.provider)
            with session_scope() as s:
                owner = owner_stt_prices(s)
            fake = choice.provider == FAKE_KEY
            cfg = self.settings.llm_providers.get(choice.provider) or {}
            per_min = (
                Decimal(0)
                if fake
                else mp.stt_per_minute(choice.provider, kind_of(self.settings, choice.provider), choice.model,
                                       cfg.get("base_url"), owner)
            )  # fmt: skip
            guard = self._guard(load_prices(self.settings))
            worst_s = mp.worst_seconds(len(audio))
            entry = guard.reserve(
                purpose=purpose, model=choice.model[:60], worst_case_usd=mp.stt_cost(per_min, worst_s),
                provider=choice.provider,
            )  # fmt: skip
        except Exception as exc:
            raise self._refuse(exc) from None
        try:
            heard = adapter.transcribe(model=choice.model, audio=audio, filename=kind[0], mime=kind[1])
        except Exception as exc:
            guard.fail(entry, type(exc).__name__)
            raise self._refuse(exc) from None
        seconds = min(heard.seconds, worst_s) if heard.seconds is not None else worst_s  # unknown: the reservation
        guard.settle(
            entry, model=choice.model[:60], usage=None, request_id=None, provider=choice.provider,
            fixed_cost_usd=mp.stt_cost(per_min, seconds),
        )  # fmt: skip
        return heard.text

    def describe(self, image: bytes, *, purpose: str = "media_vision", max_bytes: int = mp.MAX_IMAGE_BYTES) -> str:
        """A short description of a photo. Same rules as `transcribe`."""
        choice = self.choices.get("vision")
        if choice is None or not self.configured("vision"):
            raise MediaRefused("I can't look at photos yet.")
        mime = sniff_image(image)
        if mime is None:
            raise MediaRefused("I can only look at PNG, JPEG, GIF and WebP pictures.")
        if len(image) > max_bytes:
            raise MediaRefused("That picture is too large for me to look at.")
        from superaide.brain.wiring import load_prices

        try:
            adapter = build_adapter(self.settings, "vision", choice.provider)
            fake = choice.provider == FAKE_KEY
            prices = load_prices(self.settings)
            tokens_in = mp.IMAGE_TOKEN_BOUND + mp.PROMPT_TOKEN_BOUND
            if fake:
                worst = Decimal(0)
            else:
                price = prices.price_for(choice.model, choice.provider)
                rate_in, rate_out = price.rates(tokens_in)
                worst = (Decimal(tokens_in) * rate_in + Decimal(mp.DESCRIBE_MAX_TOKENS) * rate_out) / Decimal(1_000_000)
            guard = self._guard(prices)
            entry = guard.reserve(
                purpose=purpose, model=choice.model[:60], worst_case_usd=worst, provider=choice.provider
            )
        except Exception as exc:
            raise self._refuse(exc) from None
        try:
            out = adapter.describe(
                model=choice.model, image=image, mime=mime, prompt=DESCRIBE_PROMPT, max_tokens=mp.DESCRIBE_MAX_TOKENS
            )
        except Exception as exc:
            guard.fail(entry, type(exc).__name__)
            raise self._refuse(exc) from None
        guard.settle(
            entry, model=choice.model[:60], usage=UsageCounts(out.input_tokens, out.output_tokens),
            request_id=out.request_id, provider=choice.provider, known=out.known,
            fixed_cost_usd=Decimal(0) if fake else None,
        )  # fmt: skip
        return out.text

    def check(self, cap: str) -> tuple[bool, str]:
        """ "Test connection": one tiny real call through the same metered path (it counts against the cap)."""
        try:
            if cap == "vision":
                text = self.describe(sample_png(), purpose="media_vision")
            else:
                text = self.transcribe(sample_wav(), purpose="media_stt")
        except MediaRefused as exc:
            return False, str(exc)
        return True, "It works." if cap == "transcribe" or text else "It works, but the answer was empty."
