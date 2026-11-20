"""/app/media-providers: which service listens to voice notes and which looks at photos (issues #501, #511).

Owner only, next to the AI model page. Each capability picks one of the endpoints connected there (or Claude, for
photos) and a model. Nothing is sent to any service until the owner chooses one. The same price rules as text models:
a model with no known price needs the owner's price first (per million tokens for photos, per audio minute for voice
notes), and a server on this computer or the local network is free. "Test" runs one tiny real call through the spend
cap.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from superaide.app.deps import owner_person_id
from superaide.app.providers import provider_label
from superaide.brain.media import prices as mp
from superaide.brain.media import service as media
from superaide.brain.providers import config
from superaide.brain.spend.pricing import PRICES, UnknownModelPrice
from superaide.identity.auth.routes import current_user_id, reject_nul_text, require_csrf_header
from superaide.identity.vault.store import runtime_settings
from superaide.platform.db.session import session_scope

HINTS = {
    "transcribe": (
        "Needs a service with an OpenAI-style /audio/transcriptions address: OpenAI, or a Whisper server on your "
        'computer. Connect it on the AI model page first ("Other OpenAI-compatible service" for a local one).'
    ),
    "vision": (
        "Claude, OpenAI, or a local model that can see (llava, qwen-vl on Ollama, LM Studio, vLLM). "
        "Connect it on the AI model page first."
    ),
}
SUGGESTED = {
    "transcribe": ["whisper-1", "gpt-4o-mini-transcribe"],
    "vision": ["claude-haiku-5-5", "gpt-4o-mini", "llava"],
}


class ChoiceBody(BaseModel):
    provider: str = Field(min_length=1, max_length=24)
    model: str = Field(min_length=1, max_length=120)


class SttPriceBody(BaseModel):
    provider: str = Field(min_length=1, max_length=24)
    model: str = Field(min_length=1, max_length=120)
    usd_per_minute: Decimal = Field(ge=0, le=10)


def _price_state(settings: Any, cap: str, provider: str, model: str) -> dict[str, Any]:
    """Whether the spend cap can price this choice: {known, free, unit}."""
    from superaide.brain.spend.pricing import is_local_url
    from superaide.brain.wiring import load_prices

    cfg = settings.llm_providers.get(provider) or {}
    if provider == media.FAKE_KEY:
        return {"known": True, "free": True, "unit": "minute" if cap == "transcribe" else "mtok"}
    try:
        if cap == "transcribe":
            with session_scope() as s:
                owner = media.owner_stt_prices(s)
            per = mp.stt_per_minute(provider, media.kind_of(settings, provider), model, cfg.get("base_url"), owner)
            kind = media.kind_of(settings, provider)
            builtin = (provider, model) not in owner and (kind, model) in mp.STT_BUILTIN  # a list price, overridable
            return {"known": True, "free": per == 0, "unit": "minute", "usd": float(per), "builtin": builtin}
        price = load_prices(settings).price_for(model, provider)
        free = price.input == 0 and price.output == 0
        local = is_local_url(cfg.get("base_url")) if cfg else False
        return {"known": True, "free": free, "unit": "mtok", "local": local}
    except UnknownModelPrice:
        return {"known": False, "free": False, "unit": "minute" if cap == "transcribe" else "mtok"}


def make_router(apply: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/media-providers",
        tags=["Model providers"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def candidates(settings: Any, cap: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if cap == "vision":
            out.append({"key": "anthropic", "label": provider_label("anthropic"), "models": sorted(PRICES)})
        with session_scope() as s:
            mine = config.view(s)["providers"]
        for p in mine:
            out.append({"key": p["key"], "label": provider_label(p["key"]), "models": p["models"][:200]})
        if settings.fake_llm:
            out.append({"key": media.FAKE_KEY, "label": "Offline test responder", "models": ["fake"]})
        for c in out:
            c["ready"] = media.provider_ready(settings, cap, c["key"]) is None
        return out

    def view() -> dict[str, Any]:
        settings = runtime_settings()
        with session_scope() as s:
            choices, defaults = media.effective_choices(s, settings)
        caps = []
        for cap in media.CAPABILITIES:
            choice = choices.get(cap)
            ready = media.provider_ready(settings, cap, choice.provider) if choice else None
            caps.append(
                {
                    "id": cap,
                    "label": media.LABELS[cap],
                    "hint": HINTS[cap],
                    "suggested": SUGGESTED[cap],
                    "choice": {"provider": choice.provider, "model": choice.model} if choice else None,
                    "on": choice is not None and ready is None,
                    # Nobody chose this: it is on because Claude is connected. The page says where photos go.
                    "default": cap in defaults,
                    "vendor_notice": (
                        f"Photos people send are read by {provider_label(choice.provider)} using {choice.model}, which "
                        "means each photo is sent to that company. This is on because Claude is connected. "
                        "Turn it off to stop."
                    )
                    if cap in defaults and choice
                    else None,
                    "problem": ready,  # why a saved choice cannot run now (e.g. its provider was disconnected)
                    "price": _price_state(settings, cap, choice.provider, choice.model) if choice else None,
                    "providers": candidates(settings, cap),
                }
            )
        return {"capabilities": caps}

    @router.get("", summary="Voice note and photo settings", description="Which service reads voice notes and photos.")
    def get(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        return view()

    def checked(cap: str, body: ChoiceBody) -> Any:
        if cap not in media.CAPABILITIES:
            raise HTTPException(404, "Unknown setting")
        settings = runtime_settings()
        why = media.provider_ready(settings, cap, body.provider)
        if why:
            raise HTTPException(409, why)
        return settings

    @router.put(
        "/{capability}",
        summary="Choose a service",
        description="The provider and model for 'transcribe' (voice notes) or 'vision' (photos). Needs a known price.",
    )
    def choose(
        capability: str, body: ChoiceBody, _: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, Any]:  # fmt: skip
        settings = checked(capability, body)
        state = _price_state(settings, capability, body.provider, body.model)
        if not state["known"]:
            raise HTTPException(
                409, {"message": "Set a price for this model first.", "needs_price": [
                    {"provider": body.provider, "model": body.model, "unit": state["unit"]}]},
            )  # fmt: skip
        with session_scope() as s:
            media.set_choice(s, capability, body.provider, body.model, user_id=user_id)
        apply()
        return view()

    @router.post("/{capability}/remove", summary="Turn off", description="Stop reading this kind of file.")
    def remove(
        capability: str, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        if capability not in media.CAPABILITIES:
            raise HTTPException(404, "Unknown setting")
        with session_scope() as s:
            media.clear_choice(s, capability, user_id=user_id)
        apply()
        return view()

    @router.post(
        "/{capability}/test",
        summary="Test a choice",
        description="One tiny real call (a grey square, or half a second of silence). Counted against your spend cap.",
    )
    def test(capability: str, body: ChoiceBody, _: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        settings = checked(capability, body)
        svc = media.MediaService(settings, {capability: media.Choice(body.provider, body.model)})
        ok, message = svc.check(capability)
        return {"ok": ok, "message": message}

    @router.post(
        "/prices",
        summary="Set a voice-note price",
        description="US dollars per audio minute for a transcription model Superaide has no price for.",
    )
    def set_price(
        body: SttPriceBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        settings = runtime_settings()
        if media.provider_ready(settings, "transcribe", body.provider) is not None:
            raise HTTPException(404, "Unknown provider")
        with session_scope() as s:
            media.set_stt_price(s, body.provider, body.model, body.usd_per_minute, user_id=user_id)
        apply()
        return view()

    return router
