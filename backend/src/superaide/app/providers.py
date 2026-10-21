"""/app/providers: choose which AI model Ava thinks with (ADR 0007, sections 9 and 10).

Owner only. A provider is tested before it's saved; keys are encrypted in the vault and only a
masked hint ever comes back. Each role (chat, triage, deep) picks a provider and a model, and a
model with no known price can't be picked until the owner sets one, so the spend cap holds.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from superaide.app.routes import owner_person_id
from superaide.auth.routes import current_user_id, reject_nul_text, require_csrf_header
from superaide.brain.pricing import PRICES, UnknownModelPrice, set_owner_price
from superaide.brain.providers import config
from superaide.brain.providers.base import CheckReport
from superaide.brain.providers.presets import PRESETS, Preset
from superaide.brain.providers.types import Capabilities
from superaide.db.session import session_scope
from superaide.security import urls
from superaide.vault import check, store
from superaide.vault.store import runtime_settings

ANTHROPIC = {
    "id": "anthropic",
    "label": "Claude (Anthropic)",
    "base_url": "https://api.anthropic.com",
    "key_required": True,
    "key_hint": "sk-ant-…",
    "guide_url": "https://platform.claude.com/dashboard",
    "local": False,
    "note": "Recommended. Native citations and built-in web search.",
    "warnings": [],
}


def warnings(caps: Capabilities, local: bool) -> list[str]:
    out = []
    if not caps.tools:
        out.append("This model can't use tools, so Ava can't look things up or set reminders with it.")
    if not caps.native_citations:
        out.append("No built-in citations: Superaide checks every source Ava cites instead.")
    if not caps.server_web_search:
        out.append("No built-in web search: Ava can't search the web with this provider yet.")
    if local:
        out.append("Local models are free and private, but slower and less reliable with tools.")
    return out


def preset_view(p: Preset) -> dict[str, Any]:
    return {
        "id": p.id,
        "label": p.label,
        "base_url": p.base_url,
        "key_required": p.key_required,
        "key_hint": p.key_hint,
        "guide_url": p.guide_url,
        "local": p.local,
        "note": p.note,
        "warnings": warnings(p.capabilities, p.local),
    }


def build_provider(preset: str, api_key: str, base_url: str) -> Any:
    """A provider for testing a connection; replaced in tests with one that doesn't touch the network."""
    from superaide.brain.budget import BudgetGuard
    from superaide.brain.providers.openai_compat import OpenAICompatProvider

    return OpenAICompatProvider(BudgetGuard(0), PRESETS[preset], api_key=api_key, base_url=base_url)


def check_anthropic(api_key: str) -> CheckReport:
    from superaide.app.connections import http_client

    try:
        check.check_anthropic(api_key, http_client())
    except check.CheckFailed as exc:
        return CheckReport(False, str(exc))
    return CheckReport(True, "Connected to Claude", sorted(PRICES))


class ProviderBody(BaseModel):
    api_key: str | None = Field(default=None, max_length=400)  # None or omitted keeps the stored key
    base_url: str = Field(default="", max_length=300)


class TestBody(ProviderBody):
    preset: str = Field(max_length=20)


class RoleChoice(BaseModel):
    provider: str = Field(max_length=20)
    model: str = Field(min_length=1, max_length=120)


class RolesBody(BaseModel):
    chat: RoleChoice
    triage: RoleChoice | None = None
    deep: RoleChoice | None = None


class PriceBody(BaseModel):
    provider: str = Field(max_length=20)
    model: str = Field(min_length=1, max_length=120)
    input_usd_per_mtok: Decimal = Field(ge=0, le=1000)
    output_usd_per_mtok: Decimal = Field(ge=0, le=1000)


def make_router(get_agent: Callable[[], Any], apply: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/providers",
        tags=["Model providers"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def view() -> dict[str, Any]:
        from superaide.factory import brain_status

        settings = runtime_settings()
        with session_scope() as s:
            mine = config.view(s)
            claude = store.status(s, settings)["anthropic"]
        roles = {
            role: {
                "provider": config.split(getattr(settings, field))[0],
                "model": config.split(getattr(settings, field))[1],
            }
            for role, field in config.ROLE_FIELDS.items()
        }
        status = brain_status(settings)
        return {
            "presets": [ANTHROPIC, *(preset_view(p) for p in PRESETS.values())],
            "claude": {
                "connected": claude["connected"],
                "via": claude["via"],
                "key_hint": claude["hints"].get("api_key", ""),
            },
            "providers": mine["providers"],
            "roles": roles,
            "brain": {"ready": status.ready, "provider": status.provider},
        }

    def resolve(preset: str, body: ProviderBody) -> tuple[str, str]:
        """The key and base URL to use: what was typed, else what's stored. Raises 400 on a bad address."""
        if preset not in PRESETS:
            raise HTTPException(404, "Unknown provider")
        p = PRESETS[preset]
        with session_scope() as s:
            stored_key = config.key_for(s, preset)
        api_key = (body.api_key if body.api_key is not None else stored_key).strip()
        if p.key_required and not api_key:
            raise HTTPException(400, f"{p.label} needs an API key.")
        try:
            base_url = urls.check_base_url(body.base_url or p.base_url, allow_private=p.local)
        except urls.UnsafeURL as exc:
            raise HTTPException(400, str(exc)) from exc
        return api_key, base_url

    def test(preset: str, body: ProviderBody) -> CheckReport:
        if preset == "anthropic":
            if not body.api_key:
                raise HTTPException(400, "Paste your Claude API key.")
            return check_anthropic(body.api_key)
        api_key, base_url = resolve(preset, body)
        return build_provider(preset, api_key, base_url).check()

    @router.get(
        "", summary="Model settings", description="Providers you can use, those you connected, and the model per role."
    )
    def get(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        return view()

    @router.post(
        "/test",
        summary="Test a connection",
        description="Checks the key and address with the service and lists its models. Saves nothing.",
    )
    def test_connection(body: TestBody, _: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        report = test(body.preset, body)
        return {"ok": report.ok, "message": report.message, "models": report.models}

    @router.post(
        "/prices",
        summary="Set a model's price",
        description=(
            "US dollars per million input and output tokens, from the provider's price page. "
            "Counted against your spend cap."
        ),
    )
    def set_price(
        body: PriceBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        if body.provider != "anthropic" and body.provider not in PRESETS:
            raise HTTPException(404, "Unknown provider")
        with session_scope() as s:
            set_owner_price(
                s, provider=body.provider, model=body.model, input_usd_per_mtok=body.input_usd_per_mtok,
                output_usd_per_mtok=body.output_usd_per_mtok, user_id=user_id,
            )  # fmt: skip
        apply()
        return view()

    @router.post(
        "/{preset}",
        summary="Connect a provider",
        description="Tests the connection, then stores the key encrypted. Leave api_key out to keep the stored one.",
    )
    def connect(
        preset: str,
        body: ProviderBody,
        _: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, Any]:
        report = test(preset, body)
        if not report.ok:
            raise HTTPException(400, report.message)
        with session_scope() as s:
            if preset == "anthropic":
                store.save(s, "anthropic", {"api_key": (body.api_key or "").strip()}, user_id=user_id, meta={})
            else:
                api_key, base_url = resolve(preset, body)
                config.save_provider(
                    s, preset, base_url=base_url, api_key=api_key if body.api_key is not None else None,
                    user_id=user_id, models=report.models,
                )  # fmt: skip
        apply()
        return view()

    @router.post(
        "/{preset}/remove",
        summary="Disconnect a provider",
        description="Deletes its key; roles on it go back to Claude.",
    )
    def remove(
        preset: str, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            if preset == "anthropic":
                store.remove(s, "anthropic", user_id=user_id)
            elif preset in PRESETS:
                config.remove_provider(s, preset, user_id=user_id)
            else:
                raise HTTPException(404, "Unknown provider")
        apply()
        return view()

    @router.put(
        "/roles",
        summary="Choose models",
        description="The provider and model for chat (required), triage and deep work. A model needs a known price.",
    )
    def set_roles(
        body: RolesBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        from superaide.factory import load_prices, provider_usable

        settings = runtime_settings()
        prices = load_prices(settings)
        chosen = {r: c for r, c in (("chat", body.chat), ("triage", body.triage), ("deep", body.deep)) if c}
        needs_price = []
        for choice in chosen.values():
            if not provider_usable(settings, choice.provider):
                raise HTTPException(409, f"Connect {choice.provider} first.")
            try:
                prices.price_for(choice.model, choice.provider)
            except UnknownModelPrice:
                needs_price.append({"provider": choice.provider, "model": choice.model})
        if needs_price:
            raise HTTPException(409, {"message": "Set a price for these models first.", "needs_price": needs_price})
        with session_scope() as s:
            for role, choice in chosen.items():
                config.set_role(s, role, choice.provider, choice.model, user_id=user_id)
        apply()
        return view()

    return router
