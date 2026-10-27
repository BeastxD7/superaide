"""Built-in prices for OpenAI, xAI and Gemini models, in US$ per million tokens (standard tier, text).

Each table names its source and the date it was read. A model that isn't listed still needs a
price from the owner, and a price change means editing this file: the spend cap is only as right
as these numbers. OpenRouter isn't here: its models API lists each model's price, which is read
when the owner connects it (see openai_compat.check). Azure prices depend on the deployment, so
the owner sets them.

`cached` is the cached-input rate; it becomes the model's cache-read multiplier. Long-context
rates apply once a prompt passes the threshold. Where a published price is scheduled to change,
`Scheduled` switches to the new price on that date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from superaide.brain.spend.pricing import ModelPrice


def _p(inp: str, cached: str | None, out: str, *, long: tuple[int, str, str] | None = None) -> ModelPrice:
    i, o = Decimal(inp), Decimal(out)
    mult = (Decimal(cached) / i) if cached and i else Decimal(1)
    if long is None:
        return ModelPrice(i, o, mult)
    threshold, long_in, long_out = long
    return ModelPrice(i, o, mult, long_threshold=threshold, long_input=Decimal(long_in), long_output=Decimal(long_out))


@dataclass(frozen=True)
class Scheduled:
    """A price that changes on a published date."""

    before: ModelPrice
    after: ModelPrice
    on: date

    def current(self, today: date | None = None) -> ModelPrice:
        return self.after if (today or datetime.now(UTC).date()) >= self.on else self.before


# OpenAI. Source: https://developers.openai.com/api/docs/pricing (read 2026-10-22). Flagship models
# have a long-context tier above 272K input tokens. GPT-5.6 Sol is on promotional pricing "at least
# through November 21, 2026"; the page gives no regular price yet, so check it after that date.
OPENAI_SOURCE, OPENAI_AS_OF = "https://developers.openai.com/api/docs/pricing", "2026-10-22"
_LONG = 272_000
OPENAI: dict[str, ModelPrice | Scheduled] = {
    "gpt-6-astra": _p("10.00", "1.00", "50.00", long=(_LONG, "20.00", "75.00")),
    "gpt-6.1-sol": _p("2.00", "0.10", "10.00", long=(_LONG, "4.00", "15.00")),
    "gpt-6-luna": _p("0.10", "0.01", "0.50", long=(_LONG, "0.20", "0.75")),
    "gpt-6-sol": _p("2.00", "0.20", "10.00", long=(_LONG, "4.00", "15.00")),
    "gpt-5.6-sol": _p("4.00", "0.40", "20.00", long=(_LONG, "8.00", "30.00")),
    "gpt-5.6-terra": _p("2.00", "0.20", "12.00", long=(_LONG, "4.00", "18.00")),
    "gpt-5.6-luna": _p("0.20", "0.02", "1.20", long=(_LONG, "0.40", "1.80")),
    "gpt-5.5": _p("5.00", "0.50", "30.00", long=(_LONG, "10.00", "45.00")),
    "gpt-5.5-pro": _p("30.00", None, "180.00", long=(_LONG, "60.00", "270.00")),
    "gpt-5.4": _p("2.50", "0.25", "15.00", long=(_LONG, "5.00", "22.50")),
    "gpt-5.4-pro": _p("30.00", None, "180.00", long=(_LONG, "60.00", "270.00")),
    "gpt-5.4-mini": _p("0.75", "0.075", "4.50"),
    "gpt-5.4-nano": _p("0.20", "0.02", "1.25"),
    "gpt-5.2": _p("1.75", "0.175", "14.00"),
    "gpt-5.2-pro": _p("21.00", None, "168.00"),
    "gpt-5.1": _p("1.25", "0.125", "10.00"),
    "gpt-5": _p("1.25", "0.125", "10.00"),
    "gpt-5-mini": _p("0.25", "0.025", "2.00"),
    "gpt-5-nano": _p("0.05", "0.005", "0.40"),
    "gpt-5-pro": _p("15.00", None, "120.00"),
    "gpt-4.1": _p("2.00", "0.50", "8.00"),
    "gpt-4.1-mini": _p("0.40", "0.10", "1.60"),
    "gpt-4.1-nano": _p("0.10", "0.025", "0.40"),
    "gpt-4o": _p("2.50", "1.25", "10.00"),
    "gpt-4o-mini": _p("0.15", "0.075", "0.60"),
    "o3": _p("2.00", "0.50", "8.00"),
    "o3-pro": _p("20.00", None, "80.00"),
    "o4-mini": _p("1.10", "0.275", "4.40"),
    "o3-mini": _p("1.10", "0.55", "4.40"),
    "o1": _p("15.00", "7.50", "60.00"),
}

# xAI. Source: https://docs.x.ai/docs/models (read 2026-10-22). Prompts of 200K tokens or more
# are charged the higher rates. The web and X search tools aren't priced there; not used here.
XAI_SOURCE, XAI_AS_OF = "https://docs.x.ai/docs/models", "2026-10-22"
_XL = 199_999  # "≥ 200k prompt tokens"
XAI: dict[str, ModelPrice | Scheduled] = {
    "grok-4.7": _p("2.00", "0.50", "6.00", long=(_XL, "4.00", "12.00")),
    "grok-4.6": _p("2.00", "0.50", "6.00", long=(_XL, "4.00", "12.00")),
    "grok-4.5": _p("2.00", "0.30", "6.00", long=(_XL, "4.00", "12.00")),
    "grok-4.3": _p("1.25", "0.20", "2.50", long=(_XL, "2.50", "5.00")),
    "grok-4.20-0309-reasoning": _p("1.25", "0.20", "2.50", long=(_XL, "2.50", "5.00")),
    "grok-4.20-0309-non-reasoning": _p("1.25", "0.20", "2.50", long=(_XL, "2.50", "5.00")),
    "grok-4.20-multi-agent-0309": _p("1.25", "0.20", "2.50", long=(_XL, "2.50", "5.00")),
    "grok-build-0.1": _p("1.00", "0.20", "2.00", long=(_XL, "2.00", "4.00")),
}

# Gemini (paid tier). Source: https://ai.google.dev/gemini-api/docs/pricing (read 2026-10-22).
# Output includes thinking tokens. Several prices rise on published dates (Scheduled below).
GEMINI_SOURCE, GEMINI_AS_OF = "https://ai.google.dev/gemini-api/docs/pricing", "2026-10-22"
_GL = 200_000
_DEC1, _JAN1 = date(2026, 12, 1), date(2027, 1, 1)
GEMINI: dict[str, ModelPrice | Scheduled] = {
    "gemini-4-argon": Scheduled(_p("2.00", "0.10", "10.00"), _p("4.00", "0.20", "20.00"), _DEC1),
    "gemini-3.8-flash": Scheduled(_p("0.75", "0.075", "3.75"), _p("1.50", "0.15", "7.50"), _JAN1),
    "gemini-3.6-flash": Scheduled(_p("0.75", "0.075", "3.75"), _p("1.50", "0.15", "7.50"), _JAN1),
    "gemini-3.5-flash-lite": _p("0.30", "0.03", "2.50"),
    "gemini-3.1-flash-lite": _p("0.25", "0.025", "1.50"),
    "gemini-3.1-pro-preview": _p("2.00", "0.20", "12.00", long=(_GL, "4.00", "18.00")),
    "gemini-3-flash-preview": _p("0.50", "0.05", "3.00"),
    "gemini-2.5-pro": _p("1.25", "0.125", "10.00", long=(_GL, "2.50", "15.00")),
    "gemini-2.5-flash": _p("0.30", "0.03", "2.50"),
    "gemini-2.5-flash-lite": _p("0.10", "0.01", "0.40"),
}

TABLES: dict[str, dict[str, ModelPrice | Scheduled]] = {"openai": OPENAI, "xai": XAI, "gemini": GEMINI}


def lookup(kind: str, model: str) -> ModelPrice | None:
    table = TABLES.get(kind)
    if table is None:
        return None
    if kind == "gemini":
        model = model.removeprefix("models/")  # the compatibility endpoint lists "models/<id>"
    found = table.get(model)
    return found.current() if isinstance(found, Scheduled) else found
