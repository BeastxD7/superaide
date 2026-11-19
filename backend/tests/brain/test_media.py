"""#501 and #511: voice-note and photo providers. Adapters against a fake HTTP server (no network), the price and
spend-cap rules, and the settings API. The owner chooses a service per capability; unset sends nothing."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from superaide.brain.media import prices as mp
from superaide.brain.media import service as media
from superaide.brain.media.providers import (
    AnthropicVision,
    FakeTranscriber,
    FakeVision,
    MediaError,
    OpenAICompatTranscriber,
    OpenAICompatVision,
)
from superaide.brain.models import LlmProvider, ModelRole, OwnerModelPrice, SpendEntry
from superaide.brain.providers import config
from superaide.brain.providers.base import CheckReport
from superaide.brain.spend.pricing import UnknownModelPrice
from superaide.identity.vault.store import runtime_settings
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from tests.pipeline.test_loop import owner_client  # noqa: F401  (fixture)

OGG = b"OggS\x00\x02" + b"\x00" * 200
PNG = media.sample_png()
KEY = "sk-test-media-secret-0123456789"


def handler_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- the adapters --------------------------------------------------------------------------------------------


def test_openai_compatible_vision_sends_a_data_uri_and_reads_usage():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = (
            str(req.url),
            req.headers.get("authorization"),
            json.loads(req.content),
        )
        return httpx.Response(
            200, json={"id": "r1", "choices": [{"message": {"content": " A  grey\nsquare "}}],
                       "usage": {"prompt_tokens": 120, "completion_tokens": 8}},
        )  # fmt: skip

    a = OpenAICompatVision("http://localhost:11434/v1/", KEY, client=handler_client(handler))
    out = a.describe(model="llava", image=PNG, mime="image/png", prompt="describe", max_tokens=50)
    assert out.text == "A grey square" and (out.input_tokens, out.output_tokens, out.known) == (120, 8, True)
    assert seen["url"] == "http://localhost:11434/v1/chat/completions" and seen["auth"] == f"Bearer {KEY}"
    part = seen["body"]["messages"][0]["content"][1]["image_url"]["url"]
    assert part.startswith("data:image/png;base64,") and seen["body"]["max_tokens"] == 50


def test_vision_without_usage_is_marked_unknown_and_errors_never_carry_the_key_or_body():
    ok = OpenAICompatVision("http://x/v1", client=handler_client(lambda r: httpx.Response(
        200, json={"choices": [{"message": {"content": "hi"}}]})))  # fmt: skip
    assert ok.describe(model="m", image=PNG, mime="image/png", prompt="p", max_tokens=5).known is False
    for status, word in ((401, "key"), (404, "doesn't offer"), (500, "500")):
        bad = OpenAICompatVision("http://x/v1", KEY, client=handler_client(lambda r, s=status: httpx.Response(
            s, text=f"echo {KEY}")))  # fmt: skip
        with pytest.raises(MediaError, match=word) as exc:
            bad.describe(model="m", image=PNG, mime="image/png", prompt="p", max_tokens=5)
        assert KEY not in str(exc.value)


def test_whisper_style_transcription_posts_multipart_with_our_filename_and_reads_the_duration():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["ctype"], seen["raw"] = str(req.url), req.headers["content-type"], req.content
        return httpx.Response(200, json={"text": " hello there ", "duration": 4.5})

    t = OpenAICompatTranscriber("http://localhost:8000/v1", client=handler_client(handler))
    out = t.transcribe(model="whisper-1", audio=OGG, filename="voice.ogg", mime="audio/ogg")
    assert (out.text, out.seconds) == ("hello there", 4.5)
    assert seen["url"].endswith("/v1/audio/transcriptions") and seen["ctype"].startswith("multipart/form-data")
    assert b'filename="voice.ogg"' in seen["raw"] and b"verbose_json" in seen["raw"]


def test_transcription_falls_back_to_plain_json_for_models_that_only_do_json():
    formats = []

    def handler(req: httpx.Request) -> httpx.Response:
        fmt = b"verbose_json" in req.content
        formats.append("verbose" if fmt else "json")
        return httpx.Response(400) if fmt else httpx.Response(200, json={"text": "ok"})

    out = OpenAICompatTranscriber("https://api.example/v1", KEY, client=handler_client(handler)).transcribe(
        model="gpt-4o-transcribe", audio=OGG, filename="voice.ogg", mime="audio/ogg"
    )
    assert out.text == "ok" and out.seconds is None and formats == ["verbose", "json"]


def test_anthropic_vision_builds_a_base64_image_block_and_reads_usage():
    class Messages:
        def create(self, **kw):
            self.kw = kw
            return {"id": "msg_1", "content": [{"type": "text", "text": "a square"}],
                    "usage": {"input_tokens": 300, "output_tokens": 5}}  # fmt: skip

    client = type("C", (), {"messages": Messages()})()
    out = AnthropicVision("k", client=client).describe(model="claude-haiku-5-5", image=PNG, mime="image/png",
                                                       prompt="p", max_tokens=40)  # fmt: skip
    block = client.messages.kw["messages"][0]["content"][0]
    assert (
        block["type"] == "image"
        and block["source"]["type"] == "base64"
        and block["source"]["media_type"] == "image/png"
    )
    assert (out.text, out.input_tokens, out.output_tokens, out.request_id) == ("a square", 300, 5, "msg_1")


# --- file types and prices -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "name"),
    [(b"OggS" + b"\0" * 8, "voice.ogg"), (b"RIFF\0\0\0\0WAVE", "voice.wav"), (b"ID3\x04\0", "voice.mp3"),
     (b"\0\0\0\x18ftypM4A ", "voice.m4a"), (b"\x1a\x45\xdf\xa3\x01", "voice.webm"), (b"fLaC\0", "voice.flac")],
)  # fmt: skip
def test_audio_is_recognised_by_its_first_bytes_and_named_by_us(data, name):
    assert media.sniff_audio(data)[0] == name


def test_text_and_utf16_are_not_audio():
    assert media.sniff_audio(b"hello world") is None and media.sniff_audio(b"\xff\xfeh\x00i\x00") is None


def test_transcription_prices_never_guess():
    assert mp.stt_per_minute("openai", "openai", "whisper-1", None, {}) == Decimal("0.006")
    assert mp.stt_per_minute("custom", "custom", "x", "http://localhost:8000/v1", {}) == 0  # local is free
    assert mp.stt_per_minute(
        "custom", "custom", "x", "http://localhost:8000/v1", {("custom", "x"): Decimal("0.01")}
    ) == Decimal("0.01")
    with pytest.raises(UnknownModelPrice):
        mp.stt_per_minute("openrouter", "openrouter", "some-whisper", None, {})
    with pytest.raises(UnknownModelPrice):  # a remote "custom" server is not free
        mp.stt_per_minute("custom", "custom", "x", "https://8.8.8.8/v1", {})


# --- the metered service ---------------------------------------------------------------------------------------


def connect_endpoint(preset="openai", key="openai", base_url="https://api.openai.com/v1", models=("whisper-1",)):
    with session_scope() as s:
        config.save_provider(s, preset, key=key, base_url=base_url, api_key="sk-test-xyz", user_id=None,
                             models=list(models))  # fmt: skip


def choose(cap, provider, model):
    with session_scope() as s:
        media.set_choice(s, cap, provider, model, user_id=None)


@pytest.fixture
def adapters(monkeypatch):
    made = {"transcribe": FakeTranscriber(), "vision": FakeVision()}
    monkeypatch.setattr(media, "ADAPTER_FACTORY", lambda cap, key, settings: made[cap])
    return made


def ledger():
    with session_scope() as s:
        return list(s.scalars(select(SpendEntry).order_by(SpendEntry.created_at)))


def test_unset_means_nothing_is_sent(adapters):
    svc = media.MediaService(runtime_settings())
    assert not svc.configured("transcribe") and not svc.configured("vision")
    with pytest.raises(media.MediaRefused):
        svc.transcribe(OGG)
    with pytest.raises(media.MediaRefused):
        svc.describe(PNG)
    assert adapters["transcribe"].calls == [] and adapters["vision"].calls == [] and ledger() == []


def test_a_priced_transcription_reserves_then_settles_at_the_seconds_heard(adapters):
    connect_endpoint()
    choose("transcribe", "openai", "whisper-1")
    adapters["transcribe"].seconds = 30.0
    clip = OGG + b"\0" * 40_000  # bytes bound the minutes that can be billed: 8 kbit/s is the floor
    text = media.MediaService(runtime_settings()).transcribe(clip, purpose="media_stt")
    assert text.startswith("Hi, it is Priya Tan")
    [entry] = ledger()
    assert entry.status == "settled" and entry.provider == "openai" and entry.purpose == "media_stt"
    assert entry.cost_usd == Decimal("0.003000")  # 30 s at US$0.006 a minute
    assert entry.reserved_usd >= entry.cost_usd
    assert adapters["transcribe"].calls == [("whisper-1", len(clip), "voice.ogg", "audio/ogg")]


def test_a_service_that_does_not_report_seconds_is_settled_at_the_reservation(adapters):
    connect_endpoint()
    choose("transcribe", "openai", "whisper-1")
    adapters["transcribe"].seconds = None
    media.MediaService(runtime_settings()).transcribe(OGG)
    [entry] = ledger()
    assert entry.cost_usd == entry.reserved_usd > 0


def test_a_model_with_no_price_is_refused_until_the_owner_sets_one_and_a_local_server_is_free(adapters):
    connect_endpoint("openrouter", "openrouter", "https://openrouter.ai/api/v1", ("acme/whisper",))
    choose("transcribe", "openrouter", "acme/whisper")
    svc = media.MediaService(runtime_settings())
    with pytest.raises(media.MediaRefused, match="no price"):
        svc.transcribe(OGG)
    assert adapters["transcribe"].calls == [] and ledger() == []
    with session_scope() as s:
        media.set_stt_price(s, "openrouter", "acme/whisper", Decimal("0.02"), user_id=None)
    media.MediaService(runtime_settings()).transcribe(OGG)
    assert ledger()[0].cost_usd > 0
    connect_endpoint("custom", "custom", "http://localhost:9000/v1", ("small.en",))
    choose("transcribe", "custom", "small.en")
    media.MediaService(runtime_settings()).transcribe(OGG)
    assert ledger()[-1].cost_usd == 0


def test_the_spend_cap_stops_the_call_before_the_provider_is_reached(adapters, monkeypatch):
    connect_endpoint()
    choose("transcribe", "openai", "whisper-1")
    monkeypatch.setattr(runtime_settings(), "max_spend_usd", 0.0, raising=False)
    from superaide.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "max_spend_usd", 0.0)
    with pytest.raises(media.MediaRefused):
        media.MediaService(get_settings()).transcribe(OGG)
    assert adapters["transcribe"].calls == []


def test_a_failing_provider_is_recorded_as_failed_and_costs_nothing(adapters):
    connect_endpoint()
    choose("transcribe", "openai", "whisper-1")
    adapters["transcribe"].fail = lambda: MediaError("The transcription service didn't accept the key.")
    with pytest.raises(media.MediaRefused, match="didn't accept"):
        media.MediaService(runtime_settings()).transcribe(OGG)
    [entry] = ledger()
    assert entry.status == "failed" and entry.cost_usd == 0


def test_over_limit_and_wrong_type_files_never_reach_the_provider(adapters, monkeypatch):
    monkeypatch.setattr("superaide.brain.wiring.provider_usable", lambda s, k: True)
    connect_endpoint()
    choose("transcribe", "openai", "whisper-1")
    choose("vision", "anthropic", "claude-haiku-5-5")
    svc = media.MediaService(runtime_settings())
    with pytest.raises(media.MediaRefused, match="too long"):
        svc.transcribe(OGG + b"\0" * mp.MAX_AUDIO_BYTES)
    with pytest.raises(media.MediaRefused, match="common audio"):
        svc.transcribe(b"MZ" + b"\0" * 100)
    with pytest.raises(media.MediaRefused, match="too large"):
        svc.describe(PNG + b"\0" * mp.MAX_IMAGE_BYTES)
    with pytest.raises(media.MediaRefused, match="PNG, JPEG"):
        svc.describe(b"%PDF-1.7 " + b"\0" * 20)
    assert adapters["transcribe"].calls == [] and adapters["vision"].calls == []


def test_vision_through_claude_is_priced_from_usage(adapters, monkeypatch):
    monkeypatch.setattr("superaide.brain.wiring.provider_usable", lambda s, k: True)
    choose("vision", "anthropic", "claude-haiku-5-5")
    media.MediaService(runtime_settings()).describe(PNG)
    [entry] = ledger()
    # 900 input and 40 output tokens at Haiku's US$0.10 and US$0.50 per million tokens.
    assert entry.cost_usd == Decimal("0.000110") and entry.input_tokens == 900 and entry.provider == "anthropic"


def test_choosing_claude_for_voice_notes_is_refused(adapters):
    assert "does not transcribe" in (media.provider_ready(runtime_settings(), "transcribe", "anthropic") or "")


def test_media_roles_do_not_break_the_chat_model_settings():
    """The choices share `model_role` with chat/triage/deep: the overlay and the AI model page must skip them."""
    connect_endpoint()
    choose("transcribe", "openai", "whisper-1")
    choose("vision", "openai", "gpt-4o-mini")
    with session_scope() as s:
        assert "model_chat" not in config.overlay(s)  # no KeyError, and no chat model changed by a media choice
        assert set(config.view(s)["roles"]) <= {"chat", "triage", "deep"}
        assert len(list(s.scalars(select(ModelRole)))) == 2
    assert runtime_settings().llm_providers["openai"]["base_url"]


def test_disconnecting_the_endpoint_turns_its_media_choice_off():
    connect_endpoint()
    choose("transcribe", "openai", "whisper-1")
    with session_scope() as s:
        config.remove_provider(s, "openai", user_id=None)
        assert media.get_choices(s) == {}


# --- the settings API ------------------------------------------------------------------------------------------


@pytest.fixture
def service(monkeypatch):
    class Fake:
        def __call__(self, preset, api_key, base_url):
            class P:
                def check(self):
                    return CheckReport(True, "Connected", ["whisper-1", "llava", "other"])

            return P()

    monkeypatch.setattr("superaide.app.providers.build_provider", Fake())


def test_the_page_lists_both_capabilities_and_the_connected_endpoints(owner_client, service):  # noqa: F811
    owner_client.post("/app/providers/openai", json={"api_key": KEY})
    body = owner_client.get("/app/media-providers").json()
    caps = {c["id"]: c for c in body["capabilities"]}
    assert set(caps) == {"transcribe", "vision"} and caps["vision"]["choice"] is None and not caps["vision"]["on"]
    assert [p["key"] for p in caps["vision"]["providers"]][:2] == ["anthropic", "openai"]
    assert [p["key"] for p in caps["transcribe"]["providers"]][0] == "openai"  # Claude does not transcribe
    assert KEY not in json.dumps(body)


def test_choose_test_price_and_turn_off(owner_client, service, adapters):  # noqa: F811
    owner_client.post("/app/providers/openai", json={"api_key": KEY})
    owner_client.post("/app/providers/custom", json={"base_url": "http://localhost:9000/v1", "key": "custom"})
    ok = owner_client.put("/app/media-providers/transcribe", json={"provider": "openai", "model": "whisper-1"})
    assert ok.status_code == 200
    [t] = [c for c in ok.json()["capabilities"] if c["id"] == "transcribe"]
    assert t["on"] and t["choice"] == {"provider": "openai", "model": "whisper-1"} and t["price"]["usd"] == 0.006
    tested = owner_client.post(
        "/app/media-providers/transcribe/test", json={"provider": "openai", "model": "whisper-1"}
    )
    assert tested.json() == {"ok": True, "message": "It works."} and len(adapters["transcribe"].calls) == 1
    assert ledger()[-1].purpose == "media_stt"
    # An unpriced model needs the owner's price first (409 with what to set), then it can be chosen.
    need = owner_client.put("/app/media-providers/transcribe", json={"provider": "openai", "model": "acme-whisper"})
    assert need.status_code == 409 and need.json()["detail"]["needs_price"][0]["unit"] == "minute"
    priced = owner_client.post("/app/media-providers/prices", json={"provider": "openai", "model": "acme-whisper",
                                                                    "usd_per_minute": "0.01"})  # fmt: skip
    assert priced.status_code == 200
    assert (
        owner_client.put(
            "/app/media-providers/transcribe", json={"provider": "openai", "model": "acme-whisper"}
        ).status_code
        == 200
    )
    # A local endpoint is free without a price.
    local = owner_client.put("/app/media-providers/vision", json={"provider": "custom", "model": "llava"})
    assert (
        local.status_code == 200
        and [c for c in local.json()["capabilities"] if c["id"] == "vision"][0]["price"]["free"]
    )
    off = owner_client.post("/app/media-providers/vision/remove")
    assert off.status_code == 200 and all(
        c["choice"] is None or c["id"] == "transcribe" for c in off.json()["capabilities"]
    )
    with session_scope() as s:
        actions = {a.detail.get("capability") for a in s.scalars(select(AuditLog).where(AuditLog.actor == "providers"))}
        assert {"transcribe", "vision"} <= actions
        assert s.scalar(
            select(OwnerModelPrice).where(OwnerModelPrice.provider == "stt:openai")
        ).input_usd_per_mtok == Decimal("0.01")


@pytest.mark.parametrize(
    ("cap", "body", "status"),
    [
        ("transcribe", {"provider": "anthropic", "model": "claude-haiku-5-5"}, 409),  # Claude cannot transcribe
        ("transcribe", {"provider": "nowhere", "model": "m"}, 409),  # not connected
        ("vision", {"provider": "openai", "model": "gpt-4o-mini"}, 409),  # not connected here
        ("hearing", {"provider": "anthropic", "model": "m"}, 404),
        ("vision", {"provider": "", "model": "m"}, 422),
        ("vision", {"provider": "anthropic", "model": "m" * 200}, 422),
        ("vision", {"provider": "anthropic"}, 422),
    ],
)
def test_bad_choices_are_refused(owner_client, cap, body, status):  # noqa: F811
    assert owner_client.put(f"/app/media-providers/{cap}", json=body).status_code == status


def test_prices_are_bounded_and_nul_bytes_refused(owner_client, service):  # noqa: F811
    owner_client.post("/app/providers/openai", json={"api_key": KEY})
    base = {"provider": "openai", "model": "m"}
    assert owner_client.post("/app/media-providers/prices", json={**base, "usd_per_minute": "-1"}).status_code == 422
    assert owner_client.post("/app/media-providers/prices", json={**base, "usd_per_minute": "11"}).status_code == 422
    unknown = {"provider": "nowhere", "model": "m", "usd_per_minute": "1"}
    assert owner_client.post("/app/media-providers/prices", json=unknown).status_code == 404
    assert owner_client.put(
        "/app/media-providers/vision", json={"provider": "anthropic", "model": "a\u0000b"}
    ).status_code in (400, 422)


def test_nobody_but_the_owner_may_use_it(owner_client):  # noqa: F811
    from fastapi.testclient import TestClient

    anon = TestClient(owner_client.app, headers={"X-Requested-With": "superaide"})
    assert anon.get("/app/media-providers").status_code == 401
    assert anon.put("/app/media-providers/vision", json={"provider": "anthropic", "model": "m"}).status_code == 401
    no_csrf = TestClient(owner_client.app)
    no_csrf.cookies.update(owner_client.cookies)
    assert no_csrf.post("/app/media-providers/vision/remove").status_code in (400, 403)
    assert session_count(LlmProvider) == 0


def session_count(model) -> int:
    with session_scope() as s:
        return len(list(s.scalars(select(model))))
