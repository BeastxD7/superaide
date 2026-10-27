"""Realtime voice providers for the live call shell (#125). Pluggable; on with a provider key.

A realtime voice vendor does one job here: turn the caller's speech into transcript turns and speak
back the text we hand it. It is a thin shell. It holds no tools, no identity, no memory and no policy:
what the caller wants goes to the text agent as plain text, anything it proposes becomes an ordinary
approval marked voice-origin (voice/gateway.py), and only a tap inside the Superaide app confirms it.

VOICE_PROVIDER picks the provider:
- `auto` (default, #448): `deepgram` when a key is present (VOICE_PROVIDER_API_KEY or DEEPGRAM_API_KEY), else `none`.
  There is no on/off flag: adding the key turns voice on; `voice_hint` tells the owner what to add while it is off.
- `none`: live voice is off; `choose` returns a provider that refuses to open.
- `fake`: replays scripted transcript turns and records what was spoken. Used by the tests, the
  text-versus-voice experiment (evals/voice_vs_text.py) and the demo in docs/voice.md. Free, offline.
- `deepgram`: the documented hosted adapter (`DeepgramVoiceProvider`): Deepgram speech-to-text for what the caller
  said and Deepgram's voice for what the shell speaks, turn by turn. Every request goes through the budget guard
  (bucket `voice_call`, its own monthly cap inside MAX_SPEND_USD). Deepgram is already the vendor of the introduction
  call (ADR 0012), so it adds no new data holder.
- `realtime`: the adapter stub for a real vendor (`RealtimeVendorStub`). It names the methods a vendor
  adapter must fill in and refuses to run until one is written, so choosing it spends nothing.

The vendor's key is read only from the settings the vault fills (`voice_provider_api_key`, saved under
the `voice_provider` connection) and goes nowhere except the vendor's own request. Audio is never
logged or stored here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from superaide.brain.spend.budget import BudgetExceeded
from superaide.features.voice import providers
from superaide.features.voice.providers import VoiceError
from superaide.platform.config import Settings, get_settings

PROVIDER_KINDS = ("auto", "none", "fake", "realtime", "deepgram")
NEEDS_KEY_HINT = "Voice is off. Add a Deepgram key (DEEPGRAM_API_KEY, or save it under Connections) to turn it on."


def _key(s: Settings) -> str:
    return (s.voice_provider_api_key or s.deepgram_api_key or "").strip()


def resolved_kind(settings: Settings | None = None) -> str:
    """The provider in force: `auto` is deepgram with a key and none without. An unknown name is `none`."""
    s = settings or get_settings()
    kind = (s.voice_provider or "auto").strip().lower()
    if kind == "auto":
        return "deepgram" if _key(s) else "none"
    return kind if kind in PROVIDER_KINDS else "none"


def voice_available(settings: Settings | None = None) -> bool:
    """Is live voice on? Only with a provider that can run: the offline fake, or a vendor with its key.
    Never true without a key, so nothing is ever sent to a vendor unkeyed."""
    s = settings or get_settings()
    kind = resolved_kind(s)
    if kind == "fake":
        return True
    return kind in ("deepgram", "realtime") and bool(_key(s))


def voice_hint(settings: Settings | None = None) -> str | None:
    """What the owner should do to turn voice on, or None when it is on."""
    return None if voice_available(settings) else NEEDS_KEY_HINT


CALL_LANG = "en"


class RealtimeVoiceProvider(Protocol):
    """What the shell needs from a vendor. Four methods, no tools, no state about the owner."""

    name: str

    def open(self) -> None:
        """Start the call. Raises VoiceError (safe to show) if it cannot."""

    def listen(self) -> Iterator[str]:
        """The caller's turns as transcript text, one string per turn, until the call ends."""

    def speak(self, text: str) -> None:
        """Say this text to the caller. It is the only thing the shell ever sends back."""

    def close(self) -> None:
        """End the call and release the connection."""


class NoProvider:
    name = "none"

    def open(self) -> None:
        raise VoiceError("Live voice is off. Add a Deepgram key to turn it on.")

    def listen(self) -> Iterator[str]:
        return iter(())

    def speak(self, text: str) -> None:
        raise VoiceError("Live voice is not set up.")

    def close(self) -> None:
        return None


@dataclass
class FakeVoiceProvider:
    """Replays scripted transcript turns, as a vendor's speech-to-text would deliver them (mishearings
    included: the script holds what the vendor heard, not what the caller meant). Records what was spoken."""

    script: list[str] = field(default_factory=list)
    name: str = "fake"
    spoken: list[str] = field(default_factory=list)
    opened: bool = False
    closed: bool = False

    def open(self) -> None:
        self.opened = True

    def listen(self) -> Iterator[str]:
        if not self.opened:
            raise VoiceError("The call is not open.")
        yield from list(self.script)

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def close(self) -> None:
        self.closed = True


class RealtimeVendorStub:
    """Where a real vendor adapter goes (a realtime speech API with server-side transcripts).

    To add one: copy this class, give it the vendor's name, and implement the four methods with that
    vendor's streaming API over `httpx` or a websocket (no SDK, like voice/providers.py).
    Rules an adapter must keep:
    - `listen` yields transcript text only. Never audio, never a vendor "function call": the vendor
      session is created with no tools and no instructions to act.
    - `speak` sends exactly the text it is given and nothing else.
    - the key is used only in the request to the vendor, and never logged.
    - add the vendor's per-minute price to voice/prices.py so the spend cap covers the call.
    """

    name = "realtime"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def open(self) -> None:
        if not self._key:
            raise VoiceError("Live voice needs the vendor's API key. Save it under Connections.")
        raise VoiceError("No realtime voice vendor is wired in yet. See docs/voice.md.")

    def listen(self) -> Iterator[str]:
        raise VoiceError("No realtime voice vendor is wired in yet.")

    def speak(self, text: str) -> None:
        raise VoiceError("No realtime voice vendor is wired in yet.")

    def close(self) -> None:
        return None


class DeepgramVoiceProvider:
    """A hosted speech API as the realtime adapter: Deepgram transcribes each utterance the caller makes and speaks
    back the text it is handed. It is turn by turn (one request per utterance, no barge-in); full-duplex
    streaming is a later step, and the shell and the rules do not change when it comes.

    The caller's audio arrives as an iterable of utterances (`audio_in`, e.g. cut by voice activity detection in the
    browser) and the voice goes out through `audio_out(bytes)`; the telephony or browser transport is the caller's.
    Rules kept: transcripts only come out of `listen`, `speak` sends exactly the text given, the session has no
    tools and no instructions to act, the key is used only in requests to Deepgram, nothing is logged, and every
    request is reserved and settled through the budget guard (purposes voice_call_asr / voice_call_tts, bucket
    voice_call). A refusal by the guard (monthly voice cap, MAX_SPEND_USD, kill switch) ends the call; so does the
    call reaching `max_seconds` of caller audio."""

    name = "deepgram"

    def __init__(
        self,
        api_key: str,
        *,
        guard: Any,
        audio_in: Iterable[bytes],
        audio_out: Callable[[bytes], None],
        max_seconds: float = 600,
        max_turn_seconds: float = 60,
        voice: str = "aura-2-thalia-en",
        lang: str = CALL_LANG,
        client: Callable[[], httpx.Client] | None = None,
        mime: str = "audio/wav",
    ) -> None:
        self._key = api_key
        self._guard = guard
        self._audio_in = audio_in
        self._audio_out = audio_out
        self._max_seconds, self._max_turn = float(max_seconds), float(max_turn_seconds)
        self._voice, self._lang, self._mime = voice, lang, mime
        self._asr = providers.DeepgramASR(api_key, client)
        self._tts = providers.DeepgramTTS(api_key, client)
        self._heard = 0.0
        self._opened = False
        self.ended_by = ""  # "", "caller", "length" or "budget": why the call ended, for the caller's screen

    def open(self) -> None:
        if not self._key:
            raise VoiceError("Live voice needs a Deepgram key. Save it under Connections.")
        if self._guard is None:
            raise VoiceError("Live voice runs only with the spend guard.")
        self._opened = True

    def listen(self) -> Iterator[str]:
        if not self._opened:
            raise VoiceError("The call is not open.")
        for audio in self._audio_in:
            if self.ended_by == "budget":
                return
            if self._heard >= self._max_seconds:
                self.ended_by = "length"
                return
            try:
                heard = providers.metered_transcribe(
                    self._guard, self._asr, audio, self._mime, self._lang, self._max_turn, purpose="voice_call_asr"
                )
            except BudgetExceeded:
                self.ended_by = "budget"  # the call ends cleanly; the caller's screen says why
                return
            self._heard += min(heard.seconds, self._max_turn) if heard.seconds else 1.0
            if heard.text:  # silence and noise produce an empty transcript: nothing to hand on
                yield heard.text
        self.ended_by = self.ended_by or "caller"

    def speak(self, text: str) -> None:
        if not self._opened:
            raise VoiceError("The call is not open.")
        try:
            audio = providers.metered_speak(
                self._guard, self._tts, text, self._voice, self._lang, purpose="voice_call_tts"
            )
        except BudgetExceeded:
            self.ended_by = "budget"  # nothing is said; the next listen ends the call
            return
        self._audio_out(audio)

    def close(self) -> None:
        self._opened = False


def choose(
    settings: Settings | None = None,
    *,
    script: list[str] | None = None,
    guard: Any = None,
    audio_in: Iterable[bytes] = (),
    audio_out: Callable[[bytes], None] | None = None,
    client: Callable[[], httpx.Client] | None = None,
) -> RealtimeVoiceProvider:
    """The provider VOICE_PROVIDER names (`auto` follows the key). An unknown name is `none`: fail closed."""
    s = settings or get_settings()
    kind = resolved_kind(s)
    if kind == "deepgram" and not _key(s):
        return NoProvider()  # never reach a vendor without a key
    if kind == "fake":
        return FakeVoiceProvider(script=list(script or []))
    if kind == "deepgram":
        return DeepgramVoiceProvider(
            _key(s),
            guard=guard,
            audio_in=audio_in,
            audio_out=audio_out or (lambda _b: None),
            max_seconds=s.voice_call_max_seconds,
            max_turn_seconds=s.voice_max_turn_seconds,
            client=client,
        )
    if kind == "realtime":
        return RealtimeVendorStub(s.voice_provider_api_key)
    return NoProvider()


Handler = Callable[[str], str]


def run_call(provider: RealtimeVoiceProvider, handle: Handler) -> list[str]:
    """The thin shell loop: each transcript turn goes to `handle` (which returns text to say), and
    that text is spoken. Nothing else crosses: the provider gets no tools and no data."""
    spoken: list[str] = []
    provider.open()
    try:
        for heard in provider.listen():
            reply = handle(heard)
            spoken.append(reply)
            provider.speak(reply)
    finally:
        provider.close()
    return spoken
