"""Speech-to-text (ASR) and voice (TTS) providers for the voice call, one registry each (ADR 0012).

Same pattern as the model providers (ADR 0007): a small protocol, thin adapters over each vendor's
HTTP API (no SDKs), a fake for tests, and a price for every model (voice/prices.py) so the spend cap
covers speech too. "browser" isn't an adapter: it means the owner's browser does that part itself
(the Web Speech API), for free, and the server sends text only.

Which provider runs is decided in code (`choose`): the owner's own key first, then Superaide AI
credits if they opted in and the platform has a key, then the browser. Keys never leave this module
except in the request to their own service. Audio is never logged or stored.
"""

from __future__ import annotations

import io
import math
import wave
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import httpx

from superaide.config import Settings, get_settings
from superaide.voice.prices import asr_cost, tts_cost

ASR_KINDS = ("deepgram", "browser", "fake")
TTS_KINDS = ("elevenlabs", "deepgram", "browser", "fake")
VOICE_STYLES = ("formal_female", "formal_male", "neutral")

# Premade voices with a calm, professional delivery; Deepgram's Aura-2 voices are English-only.
# George is the voice in ElevenLabs' own examples; check the others with GET /v1/voices (premade).
VOICES: dict[str, dict[str, str]] = {
    "elevenlabs": {
        "formal_female": "Xb7hH8MSUJpSbSDYk0k2",  # Alice: British, clear, confident
        "formal_male": "JBFqnCBsd6RMkjVDRZzb",  # George: warm, measured British male
        "neutral": "SAz9YHcvj6GT2YYXdXww",  # River: relaxed, neutral
    },
    "deepgram": {
        "formal_female": "aura-2-athena-en",
        "formal_male": "aura-2-orpheus-en",
        "neutral": "aura-2-thalia-en",
    },
}
VOICE_LABELS = {
    "formal_female": "Formal, female",
    "formal_male": "Formal, male",
    "neutral": "Neutral",
}


class VoiceError(RuntimeError):
    """A provider failed; the message is safe to show (it never contains a key or audio)."""


@dataclass(frozen=True)
class Transcript:
    text: str
    seconds: float  # how much audio was heard, which is what's billed


class ASR(Protocol):
    name: str
    model: str

    def transcribe(self, audio: bytes, mime: str, lang: str) -> Transcript: ...


class TTS(Protocol):
    name: str
    model: str
    mime: str

    def synthesize(self, text: str, voice: str, lang: str) -> bytes: ...


def http_client() -> httpx.Client:
    """Replaced in tests with a client that doesn't touch the network."""
    return httpx.Client(timeout=httpx.Timeout(20, connect=5))


def _short_lang(lang: str) -> str:
    return (lang or "en").replace("_", "-").split("-")[0].lower() or "en"


# --- Deepgram -----------------------------------------------------------------------------------

# Nova-3's monolingual languages; anything else uses its multilingual mode.
DEEPGRAM_LANGS = {"en", "es", "fr", "de", "hi", "it", "ja", "ko", "nl", "pt", "ru", "sv", "tr", "uk", "da", "no"}


class DeepgramASR:
    name, model = "deepgram", "nova-3"

    def __init__(self, api_key: str, client: Callable[[], httpx.Client] | None = None) -> None:
        self._key = api_key
        self._client = client or http_client

    def transcribe(self, audio: bytes, mime: str, lang: str) -> Transcript:
        short = _short_lang(lang)
        params = {
            "model": self.model,
            "smart_format": "true",
            "punctuate": "true",
            "language": short if short in DEEPGRAM_LANGS else "multi",
        }
        try:
            r = self._client().post(
                "https://api.deepgram.com/v1/listen",
                params=params,
                content=audio,
                headers={"Authorization": f"Token {self._key}", "Content-Type": mime or "audio/wav"},
            )
        except httpx.HTTPError as exc:
            raise VoiceError("Couldn't reach Deepgram") from exc
        if r.status_code in (401, 403):
            raise VoiceError("Deepgram didn't accept the key")
        if r.status_code >= 400:
            raise VoiceError(f"Deepgram answered with an error ({r.status_code})")
        body: dict[str, Any] = r.json()
        try:
            text = body["results"]["channels"][0]["alternatives"][0]["transcript"]
        except KeyError, IndexError, TypeError:
            text = ""
        seconds = float((body.get("metadata") or {}).get("duration") or 0.0)
        return Transcript(" ".join(str(text).split()), seconds)


class DeepgramTTS:
    name, model, mime = "deepgram", "aura-2", "audio/mpeg"

    def __init__(self, api_key: str, client: Callable[[], httpx.Client] | None = None) -> None:
        self._key = api_key
        self._client = client or http_client

    def synthesize(self, text: str, voice: str, lang: str) -> bytes:
        try:
            r = self._client().post(
                "https://api.deepgram.com/v1/speak",
                params={"model": voice, "encoding": "mp3"},
                json={"text": text},
                headers={"Authorization": f"Token {self._key}"},
            )
        except httpx.HTTPError as exc:
            raise VoiceError("Couldn't reach Deepgram") from exc
        if r.status_code >= 400:
            raise VoiceError(f"Deepgram's voice answered with an error ({r.status_code})")
        return r.content


# --- ElevenLabs ---------------------------------------------------------------------------------


class ElevenLabsTTS:
    name, model, mime = "elevenlabs", "eleven_flash_v2_5", "audio/mpeg"

    def __init__(self, api_key: str, client: Callable[[], httpx.Client] | None = None) -> None:
        self._key = api_key
        self._client = client or http_client

    def synthesize(self, text: str, voice: str, lang: str) -> bytes:
        body: dict[str, Any] = {
            "text": text,
            "model_id": self.model,
            # Steadier and less theatrical than the defaults: a composed, professional delivery.
            "voice_settings": {"stability": 0.6, "similarity_boost": 0.8, "style": 0.0, "speed": 1.0},
            "language_code": _short_lang(lang),
        }
        try:
            r = self._client().post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream",
                params={"output_format": "mp3_44100_64"},
                json=body,
                headers={"xi-api-key": self._key},
            )
        except httpx.HTTPError as exc:
            raise VoiceError("Couldn't reach ElevenLabs") from exc
        if r.status_code in (401, 403):
            raise VoiceError("ElevenLabs didn't accept the key")
        if r.status_code >= 400:
            raise VoiceError(f"ElevenLabs answered with an error ({r.status_code})")
        return r.content


# --- Fakes (tests and the end-to-end suite) -------------------------------------------------------

FAKE_ANSWERS = [
    "I'm Jordan. I lead applied AI at a design consultancy in Singapore.",
    "Mostly client workshops and research. My wife Priya and my co-founder Arjun should always get through.",
    "Message me on Telegram if it's urgent. Please keep quiet after ten at night.",
    "You can say I'm in a meeting, but never share my phone number.",
]


class FakeASR:
    """Hears a scripted answer for any audio: one line after another, then the first again."""

    name, model = "fake", "fake"

    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = answers or FAKE_ANSWERS
        self.calls: list[tuple[int, str, str]] = []

    def transcribe(self, audio: bytes, mime: str, lang: str) -> Transcript:
        self.calls.append((len(audio), mime, lang))
        text = self.answers[(len(self.calls) - 1) % len(self.answers)]
        return Transcript(text, seconds=max(1.0, len(audio) / 32_000))


def silent_wav(seconds: float = 0.25, rate: int = 16_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * math.ceil(seconds * rate))
    return buf.getvalue()


class FakeTTS:
    """A short silence per sentence: the browser plays something without a voice service."""

    name, model, mime = "fake", "fake", "audio/wav"

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def synthesize(self, text: str, voice: str, lang: str) -> bytes:
        self.spoken.append(text)
        return silent_wav()


# One of each per process, so a scripted conversation carries on from turn to turn (VOICE_ASR=fake).
SHARED_FAKES: dict[str, Any] = {"asr": FakeASR(), "tts": FakeTTS()}


# --- Choosing and metering ----------------------------------------------------------------------


@dataclass
class Chosen[P]:
    """A provider and who pays for it, or none: the browser does it."""

    kind: str  # e.g. "deepgram", or "browser"
    provider: P | None = None
    key_source: str = "owner"  # owner | platform


def _platform_ok(settings: Settings) -> bool:
    from superaide.factory import credits_on

    return credits_on(settings)


def choose_asr(settings: Settings, *, fakes: dict[str, Any] | None = None) -> Chosen[ASR]:
    want = (settings.voice_asr or "auto").lower()
    if want == "fake":
        return Chosen("fake", (fakes or {}).get("asr") or SHARED_FAKES["asr"])
    if want == "browser":
        return Chosen("browser")
    if want in ("auto", "deepgram"):
        if settings.deepgram_api_key:
            return Chosen("deepgram", DeepgramASR(settings.deepgram_api_key))
        env = get_settings()  # the platform key is never in an owner's settings
        if _platform_ok(settings) and env.platform_deepgram_api_key:
            return Chosen("deepgram", DeepgramASR(env.platform_deepgram_api_key), "platform")
    return Chosen("browser")


def choose_tts(settings: Settings, lang: str, *, fakes: dict[str, Any] | None = None) -> Chosen[TTS]:
    want = (settings.voice_tts or "auto").lower()
    if want == "fake":
        return Chosen("fake", (fakes or {}).get("tts") or SHARED_FAKES["tts"])
    if want == "browser":
        return Chosen("browser")
    env = get_settings()
    platform = _platform_ok(settings)
    english = _short_lang(lang) == "en"  # Aura-2 speaks English (and Spanish) only
    if want in ("auto", "elevenlabs"):
        if settings.elevenlabs_api_key:
            return Chosen("elevenlabs", ElevenLabsTTS(settings.elevenlabs_api_key))
        if platform and env.platform_elevenlabs_api_key:
            return Chosen("elevenlabs", ElevenLabsTTS(env.platform_elevenlabs_api_key), "platform")
    if want in ("auto", "deepgram") and english:
        if settings.deepgram_api_key:
            return Chosen("deepgram", DeepgramTTS(settings.deepgram_api_key))
        if platform and env.platform_deepgram_api_key:
            return Chosen("deepgram", DeepgramTTS(env.platform_deepgram_api_key), "platform")
    return Chosen("browser")


def voice_id(kind: str, style: str) -> str:
    style = style if style in VOICE_STYLES else "formal_female"
    return VOICES.get(kind, {}).get(style, style)


def metered_transcribe(guard: Any, asr: ASR, audio: bytes, mime: str, lang: str, max_seconds: float) -> Transcript:
    """Reserve the longest answer we accept, transcribe, settle at the seconds heard."""
    worst = asr_cost(asr.name, asr.model, max_seconds)
    entry = guard.reserve(purpose="voice_asr", model=asr.model, worst_case_usd=worst, provider=asr.name)
    try:
        heard = asr.transcribe(audio, mime, lang)
    except Exception as exc:
        guard.fail(entry, f"{type(exc).__name__}: {exc}")
        raise
    cost = asr_cost(asr.name, asr.model, min(heard.seconds, max_seconds) if heard.seconds else 1)
    guard.settle(entry, model=asr.model, usage=None, request_id=None, provider=asr.name, fixed_cost_usd=cost)
    return heard


def metered_speak(guard: Any, tts: TTS, text: str, voice: str, lang: str) -> bytes:
    """A voice is billed by the characters sent, which are known up front."""
    cost: Decimal = tts_cost(tts.name, tts.model, len(text))
    entry = guard.reserve(purpose="voice_tts", model=tts.model, worst_case_usd=cost, provider=tts.name)
    try:
        audio = tts.synthesize(text, voice, lang)
    except Exception as exc:
        guard.fail(entry, f"{type(exc).__name__}: {exc}")
        raise
    guard.settle(entry, model=tts.model, usage=None, request_id=None, provider=tts.name, fixed_cost_usd=cost)
    return audio
