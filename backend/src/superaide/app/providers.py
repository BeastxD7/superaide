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
        out.append("No built-in web search: Ava reads pages with Superaide's own tool, and searches with a Brave key.")
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
    # Another endpoint of the same kind, e.g. "custom-2"; omitted means the preset's own endpoint.
    key: str | None = Field(default=None, pattern=config.KEY_RE, max_length=24)


class TestBody(ProviderBody):
    preset: str = Field(max_length=20)


class SearchKeyBody(BaseModel):
    api_key: str = Field(min_length=10, max_length=200)


def web_search_view(settings: Any) -> dict[str, Any]:
    """Whether Superaide's own web search has a key, and how it's set. Never the key."""
    from superaide.config import get_settings

    with session_scope() as s:
        row = store.stored(s).get(("brave", "api_key"))
    via = "app" if row else ("env" if get_settings().brave_search_api_key else None)
    return {
        "connected": via is not None,
        "via": via,
        "key_hint": row.hint if row else "",
        "usd_per_call": settings.web_search_usd_per_call,
    }


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
        from superaide.factory import brain_status, load_prices

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
        prices = load_prices(settings)

        def priced(provider: str, models: list[str]) -> list[str]:
            out = []
            for m in models:
                try:
                    prices.price_for(m, provider)
                    out.append(m)
                except UnknownModelPrice:
                    pass
            return out

        known = {"anthropic": sorted(PRICES)}
        known.update({p["key"]: priced(p["key"], p["models"]) for p in mine["providers"]})
        for role in roles.values():  # a model typed in by hand, already chosen
            if role["model"] not in known.get(role["provider"], []):
                known.setdefault(role["provider"], []).extend(priced(role["provider"], [role["model"]]))
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
            "web_search": web_search_view(settings),
            "priced": known,  # models whose price Superaide knows, per provider
        }

    def resolve(preset: str, body: ProviderBody) -> tuple[str, str]:
        """The key and base URL to use: what was typed, else what's stored. Raises 400 on a bad address."""
        if preset not in PRESETS:
            raise HTTPException(404, "Unknown provider")
        p = PRESETS[preset]
        with session_scope() as s:
            stored_key = config.key_for(s, body.key or preset)
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

    def search_check(api_key: str) -> CheckReport:
        """One real search, charged against the cap like any other."""
        from decimal import Decimal

        from superaide.brain.budget import BudgetExceeded, BudgetGuard, Limits
        from superaide.tools.web import WebTools

        settings = runtime_settings()
        guard = BudgetGuard(settings.max_spend_usd, limits=Limits.from_settings(settings))
        tools = WebTools(guard, search_key=api_key, search_usd=Decimal(str(settings.web_search_usd_per_call)))
        try:
            out = tools.run("web_search", {"query": "Superaide personal assistant"})
        except BudgetExceeded as exc:
            return CheckReport(False, f"Not tested: {exc}")
        content, is_error = out if out is not None else ("Search isn't available.", True)
        if is_error:
            message = str(content)
            if "(401)" in message or "(403)" in message or "(422)" in message:
                message = "Brave didn't accept that key."
            return CheckReport(False, message)
        return CheckReport(True, "Web search works.")

    @router.post(
        "/web-search/test",
        summary="Test a web search key",
        description="Runs one Brave search with the key (charged against your cap). Saves nothing.",
    )
    def test_search(body: SearchKeyBody, _: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        report = search_check(body.api_key.strip())
        return {"ok": report.ok, "message": report.message, "models": []}

    @router.post(
        "/web-search",
        summary="Turn on web search",
        description="Tests the Brave Search key, then stores it encrypted. Used by models without built-in search.",
    )
    def save_search(
        body: SearchKeyBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        report = search_check(body.api_key.strip())
        if not report.ok:
            raise HTTPException(400, report.message)
        with session_scope() as s:
            store.save(s, "brave", {"api_key": body.api_key.strip()}, user_id=user_id, meta={})
        apply()
        return view()

    @router.post("/web-search/remove", summary="Turn off web search", description="Deletes the stored search key.")
    def remove_search(
        _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            store.remove(s, "brave", user_id=user_id)
        apply()
        return view()

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
        with session_scope() as s:
            if (
                body.provider != "anthropic"
                and body.provider not in PRESETS
                and config.endpoint(s, body.provider) is None
            ):
                raise HTTPException(404, "Unknown provider")
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
                existing = config.endpoint(s, body.key or preset)
                if existing is not None and existing.preset != preset:
                    raise HTTPException(409, f"{body.key} is already used by another provider.")
                config.save_provider(
                    s, preset, key=body.key or preset, base_url=base_url,
                    api_key=api_key if body.api_key is not None else None,
                    user_id=user_id, models=report.models, prices=report.prices,
                )  # fmt: skip
        apply()
        return view()

    @router.post(
        "/{key}/remove",
        summary="Disconnect a provider",
        description="Deletes its key; roles on it go back to Claude.",
    )
    def remove(
        key: str, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            if key == "anthropic":
                store.remove(s, "anthropic", user_id=user_id)
            elif config.endpoint(s, key) is not None or key in PRESETS:
                config.remove_provider(s, key, user_id=user_id)
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
