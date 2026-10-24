"""Voice prices in US dollars: speech-to-text per audio minute, voices per 1,000 characters.

Like brain/pricing.py, a provider/model pair with no price here is refused, so a voice call can
never get past the spend cap unpriced. Each row names its source and when it was read. These are
the pay-as-you-go list prices; a plan with included minutes or characters costs less, so the cap
errs on the safe side.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal


class UnknownVoicePrice(KeyError):
    pass


@dataclass(frozen=True)
class VoicePrice:
    usd: Decimal
    unit: str  # "minute" (of audio heard) or "kchar" (1,000 characters spoken)
    source: str
    as_of: str


# Speech-to-text, billed per audio minute. Deepgram bills pre-recorded audio by the second.
ASR: dict[tuple[str, str], VoicePrice] = {
    ("deepgram", "nova-3"): VoicePrice(
        Decimal("0.0043"), "minute", "https://deepgram.com/pricing (Nova-3 monolingual, pre-recorded)", "2026-10-22"
    ),
    ("fake", "fake"): VoicePrice(Decimal(0), "minute", "tests", "2026-10-22"),
}

# Voices, billed per character sent.
TTS: dict[tuple[str, str], VoicePrice] = {
    ("elevenlabs", "eleven_flash_v2_5"): VoicePrice(
        Decimal("0.04"), "kchar", "https://elevenlabs.io/pricing/api (Flash v2.5)", "2026-10-22"
    ),
    ("deepgram", "aura-2"): VoicePrice(
        Decimal("0.030"), "kchar", "https://deepgram.com/pricing (Aura-2, pay as you go)", "2026-10-22"
    ),
    ("fake", "fake"): VoicePrice(Decimal(0), "kchar", "tests", "2026-10-22"),
}

# A spoken minute is about 150 words, roughly 900 characters.
CHARS_PER_MINUTE = 900


def asr_price(provider: str, model: str) -> VoicePrice:
    try:
        return ASR[(provider, model)]
    except KeyError:
        raise UnknownVoicePrice(f"No price for speech-to-text {provider}/{model}") from None


def tts_price(provider: str, model: str) -> VoicePrice:
    try:
        return TTS[(provider, model)]
    except KeyError:
        raise UnknownVoicePrice(f"No price for voice {provider}/{model}") from None


def asr_cost(provider: str, model: str, seconds: float) -> Decimal:
    """Billed by the whole second, never less than one."""
    whole = max(1, math.ceil(max(0.0, seconds)))
    return (asr_price(provider, model).usd * Decimal(whole) / Decimal(60)).quantize(Decimal("0.000001"))


def tts_cost(provider: str, model: str, chars: int) -> Decimal:
    return (tts_price(provider, model).usd * Decimal(max(0, chars)) / Decimal(1000)).quantize(Decimal("0.000001"))


def per_minute(asr: tuple[str, str] | None, tts: tuple[str, str] | None, *, owner_share: float = 0.5) -> Decimal:
    """What one minute of the call costs in speech: the owner talks `owner_share` of it, Ava the rest.
    The Claude turns are counted separately, by the model's own price."""
    total = Decimal(0)
    if asr is not None:
        total += asr_price(*asr).usd * Decimal(str(owner_share))
    if tts is not None:
        total += tts_price(*tts).usd * Decimal(CHARS_PER_MINUTE) / Decimal(1000) * Decimal(str(1 - owner_share))
    return total.quantize(Decimal("0.0001"))
