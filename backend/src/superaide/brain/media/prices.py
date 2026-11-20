"""What speech-to-text costs, and the bounds the spend cap reserves for a photo or a voice note.

Vision models are priced like text models (per million tokens, `brain/spend/pricing.py`: the built-in tables, the
owner's price, a local server is free). Transcription is billed per audio minute, so it has its own small table here;
a model with no price is refused until the owner sets one, and a server on this machine or the local network is free.
The owner's per-minute price lives in `owner_model_price` under the provider name `stt:<endpoint key>`, in the
`input_usd_per_mtok` column (read as US$ per audio minute: no table of its own was needed).

Prices are OpenAI's published list prices, checked 2026-11-20 (https://platform.openai.com/docs/pricing).
"""

from __future__ import annotations

from decimal import Decimal

from superaide.brain.spend.pricing import LOCAL_KINDS, UnknownModelPrice, is_local_url

STT_BUILTIN: dict[tuple[str, str], Decimal] = {
    ("openai", "whisper-1"): Decimal("0.006"),
    ("openai", "gpt-4o-transcribe"): Decimal("0.006"),
    ("openai", "gpt-4o-mini-transcribe"): Decimal("0.003"),
}

MIN_BYTES_PER_SECOND = 1_000  # 8 kbit/s: no speech codec we accept is thinner, so bytes bound the minutes billed
MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Anthropic's per-image limit; the others are looser
# A photo is billed by size, never more than a few thousand tokens; this is a generous fixed bound per image.
IMAGE_TOKEN_BOUND = 8_000
PROMPT_TOKEN_BOUND = 400
DESCRIBE_MAX_TOKENS = 300


def stt_owner_provider(key: str) -> str:
    return f"stt:{key}"


def worst_seconds(size: int) -> float:
    return size / MIN_BYTES_PER_SECOND


def stt_per_minute(
    key: str, kind: str, model: str, base_url: str | None, owner: dict[tuple[str, str], Decimal]
) -> Decimal:
    """US$ per audio minute: the owner's price, the table, free on a local server. Never a guess."""
    if (key, model) in owner:
        return owner[(key, model)]
    if (kind, model) in STT_BUILTIN:
        return STT_BUILTIN[(kind, model)]
    if kind in LOCAL_KINDS and is_local_url(base_url):
        return Decimal(0)
    raise UnknownModelPrice(f"No price for speech-to-text {key} model {model!r}; set one in the app.")


def stt_cost(per_minute: Decimal, seconds: float) -> Decimal:
    return (per_minute * Decimal(str(seconds)) / Decimal(60)).quantize(Decimal("0.000001"))
