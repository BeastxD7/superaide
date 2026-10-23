"""Realtime voice providers for the live call shell (#125). Pluggable, off by default, nothing bought.

A realtime voice vendor does one job here: turn the caller's speech into transcript turns and speak
back the text we hand it. It is a thin shell. It holds no tools, no identity, no memory and no policy:
what the caller wants goes to the text agent as plain text, anything it proposes becomes an ordinary
approval marked voice-origin (voice/gateway.py), and only a tap inside the Superaide app confirms it.

VOICE_PROVIDER picks the provider:
- `none` (default): live voice is off; `choose` returns a provider that refuses to open.
- `fake`: replays scripted transcript turns and records what was spoken. Used by the tests, the
  text-versus-voice experiment (evals/voice_vs_text.py) and the demo in docs/voice.md. Free, offline.
- `realtime`: the adapter stub for a real vendor (`RealtimeVendorStub`). It names the methods a vendor
  adapter must fill in and refuses to run until one is written, so choosing it spends nothing.

The vendor's key is read only from the settings the vault fills (`voice_provider_api_key`, saved under
the `voice_provider` connection) and goes nowhere except the vendor's own request. Audio is never
logged or stored here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

from superaide.config import Settings, get_settings
from superaide.voice.providers import VoiceError

PROVIDER_KINDS = ("none", "fake", "realtime")


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
        raise VoiceError("Live voice is not set up. Set VOICE_PROVIDER to try it.")

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


def choose(settings: Settings | None = None, *, script: list[str] | None = None) -> RealtimeVoiceProvider:
    """The provider VOICE_PROVIDER names. An unknown name is `none`: fail closed."""
    s = settings or get_settings()
    kind = (s.voice_provider or "none").strip().lower()
    if kind == "fake":
        return FakeVoiceProvider(script=list(script or []))
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
