"""The introduction's voice call, v2 (ADR 0012): turn-taking, barge-in, budget caps, consent before
facts are saved, per-instance isolation, and the providers (with fake streams, never the network)."""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from superaide.config import get_settings
from superaide.db.models import AuditLog, MemoryItem, Onboarding, SpendEntry
from superaide.db.session import session_scope
from superaide.knowledge.facts import Fact, FactList
from superaide.voice import providers
from superaide.voice.interview import TOPICS, ReplyReader, split_sentences
from superaide.voice.prices import asr_cost, per_minute, tts_cost
from tests.api.conftest import OTHER, hire, signup

WAV = providers.silent_wav(1.0)


def events(r: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    assert r.status_code == 200, r.text
    out = []
    for chunk in r.text.split("\n\n"):
        kind, data = "", ""
        for line in chunk.splitlines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if kind:
            out.append((kind, json.loads(data)))
    return out


def of(evs, kind: str) -> list[dict[str, Any]]:
    return [d for k, d in evs if k == kind]


@pytest.fixture
def fakes(monkeypatch):
    f = {"asr": providers.FakeASR(), "tts": providers.FakeTTS()}
    monkeypatch.setattr(providers, "SHARED_FAKES", f)
    return f


@pytest.fixture
def voice_on(monkeypatch, settings, fakes):
    monkeypatch.setattr(settings, "voice_intro_v2", True)
    monkeypatch.setattr(settings, "voice_asr", "fake")
    monkeypatch.setattr(settings, "voice_tts", "fake")
    return settings


@pytest.fixture
def scripted(agent, monkeypatch):
    """A Claude brain whose voice turns come from a list; fact extraction returns two facts."""
    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: True)
    replies: list[str] = []
    seen: list[dict[str, Any]] = []

    def responder(purpose, system, messages, output_model):
        if purpose == "voice_turn":
            seen.append({"system": system[0].text, "messages": messages})
            return replies.pop(0)
        if purpose == "intro_facts":
            return FactList(
                facts=[
                    Fact(content="Jordan leads applied AI in Singapore.", category="work", confidence=1),
                    Fact(content="Priya, Jordan's wife, can always get through.", category="person", confidence=1),
                ]
            )
        raise AssertionError(purpose)

    agent.llm.responder = responder
    return replies, seen


def call_state() -> dict[str, Any]:
    with session_scope() as s:
        row = s.scalars(select(Onboarding)).one()
        return dict((row.answers or {}).get("voice_call") or {})


def say(client, *, audio: bytes | None = WAV, text: str = "", heard: str | None = None):
    data = {"text": text, "lang": "en-GB"}
    if heard is not None:
        data["heard"], data["interrupted"] = heard, "true"
    files = {"audio": ("answer.wav", audio, "audio/wav")} if audio is not None else None
    return events(client.post("/app/intro/voice/turn", data=data, files=files))


# --- the flag ------------------------------------------------------------------------------------


def test_every_voice_route_is_404_for_the_owner_while_off(owner):
    assert owner.get("/app/intro/voice").status_code == 404
    for path in ("/start", "/turn", "/finish", "/confirm", "/settings"):
        assert owner.post(f"/app/intro/voice{path}", json={}).status_code == 404, path


# --- turn-taking ---------------------------------------------------------------------------------


def test_the_call_greets_then_takes_one_turn_per_utterance(owner, voice_on, fakes):
    cfg = owner.get("/app/intro/voice").json()
    assert cfg["asr"] == "fake" and cfg["tts"] == "fake" and cfg["brain"] == "scripted" and cfg["topics"] == 6
    start = events(owner.post("/app/intro/voice/start", json={"lang": "en-GB"}))
    sentences = [d["text"] for d in of(start, "sentence")]
    assert sentences[0].startswith("Hello Jordan, I'm Ava.") and TOPICS[0].question in " ".join(sentences)
    audio = of(start, "audio")
    assert len(audio) == len(sentences) and base64.b64decode(audio[0]["b64"])[:4] == b"RIFF"
    assert of(start, "done")[0]["timings"]["first_audio_ms"] >= 0

    turn = say(owner)
    assert of(turn, "transcript")[0]["text"] == providers.FAKE_ANSWERS[0]
    said = " ".join(d["text"] for d in of(turn, "sentence"))
    assert TOPICS[1].question in said  # the scripted interviewer moved to the next topic
    done = of(turn, "done")[0]
    assert done["topic"] == 1 and not done["ended"] and {"asr_ms", "first_text_ms", "total_ms"} <= set(done["timings"])
    assert fakes["asr"].calls[0][1:] == ("audio/wav", "en-GB")
    # The conversation keeps both sides, marked as the call; no audio is kept anywhere.
    msgs = owner.get("/app/messages").json()
    assert msgs[-2]["text"] == providers.FAKE_ANSWERS[0] and msgs[-2]["from"] == "you"
    assert call_state()["turns"][-1]["role"] == "ava"
    with session_scope() as s:
        assert not any("RIFF" in (r.detail or {}).__repr__() for r in s.scalars(select(AuditLog)))


def test_text_from_the_browsers_speech_or_typing_skips_server_speech_to_text(owner, voice_on, fakes):
    events(owner.post("/app/intro/voice/start", json={}))
    turn = say(owner, audio=None, text="I run a small studio.")
    assert not of(turn, "transcript") and not fakes["asr"].calls
    assert call_state()["turns"][-2] == {"role": "owner", "text": "I run a small studio."}


def test_silence_or_an_empty_transcript_is_not_a_turn(owner, voice_on, fakes):
    events(owner.post("/app/intro/voice/start", json={}))
    fakes["asr"].answers = [""]
    turn = say(owner)
    assert of(turn, "done")[0].get("empty") and not of(turn, "sentence")
    assert len(call_state()["turns"]) == 1
    assert owner.post("/app/intro/voice/turn", data={"text": "  "}).status_code == 422


def test_the_whole_scripted_interview_ends_after_the_last_topic(owner, voice_on):
    events(owner.post("/app/intro/voice/start", json={}))
    for i in range(len(TOPICS)):
        done = of(say(owner, audio=None, text=f"answer {i}"), "done")[0]
    assert done["ended"] and call_state()["ended"]
    assert of(say(owner, audio=None, text="one more"), "error")


def test_claude_asks_one_follow_up_at_most_then_moves_on(owner, voice_on, scripted):
    replies, seen = scripted
    events(owner.post("/app/intro/voice/start", json={}))
    replies.append("[follow] A design consultancy in Singapore. What kind of clients do you work with most?")
    turn = say(owner)
    assert " ".join(d["text"] for d in of(turn, "sentence")).startswith("A design consultancy")
    assert call_state()["topic"] == 0 and call_state()["follow_ups"] == 1
    assert "CURRENT" in seen[0]["system"] and seen[0]["messages"][0]["role"] == "user"
    replies.append("[follow] And how big is the team?")
    say(owner, audio=None, text="Mostly banks.")
    assert call_state()["topic"] == 1  # code, not the model, caps follow-ups at one per topic
    assert "do not use [follow]" in seen[1]["system"]


def test_the_model_ending_the_call_and_the_tag_never_spoken(owner, voice_on, scripted):
    replies, _ = scripted
    events(owner.post("/app/intro/voice/start", json={}))
    replies.append("[end] Thank you, Jordan. I'll show you what I've learned.")
    turn = say(owner)
    assert all("[" not in d["text"] for d in of(turn, "sentence"))
    assert of(turn, "done")[0]["ended"]


def test_barge_in_keeps_only_what_the_owner_heard(owner, voice_on, scripted):
    replies, seen = scripted
    events(owner.post("/app/intro/voice/start", json={}))
    turn = say(owner, audio=None, text="Hi", heard="Hello Jordan, I'm Ava. Thank you")
    replies.append("[next] Of course. What are you focused on at the moment?")
    turn = say(owner, audio=None, text="Sorry, go on", heard="Hello Jordan, I'm Ava. Thank you")
    assert of(turn, "done")
    turns = call_state()["turns"]
    interrupted = [t for t in turns if t.get("interrupted")]
    assert interrupted and interrupted[0]["text"].startswith("Hello Jordan, I'm Ava. Thank you")
    assert any("[they interrupted here]" in m["content"] for m in seen[-1]["messages"])


def test_talking_again_before_ava_answered_marks_her_reply_unheard(owner, voice_on):
    events(owner.post("/app/intro/voice/start", json={}))
    say(owner, audio=None, text="I'm a designer.", heard="")
    assert call_state()["turns"][0] == {"role": "ava", "text": "…", "interrupted": True}


def test_a_failing_model_falls_back_to_the_script(owner, voice_on, agent, monkeypatch):
    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: True)

    def boom(purpose, system, messages, output_model):
        raise RuntimeError("provider down")

    agent.llm.responder = boom
    events(owner.post("/app/intro/voice/start", json={}))
    turn = say(owner, audio=None, text="skip that one")
    assert "No problem" in of(turn, "sentence")[0]["text"]


# --- consent: facts only after the owner confirms -------------------------------------------------


def intro_call_facts() -> list[MemoryItem]:
    with session_scope() as s:
        rows = s.scalars(select(MemoryItem).where(MemoryItem.source == "intro_call")).all()
        s.expunge_all()
        return list(rows)


def test_facts_are_proposed_at_the_end_and_saved_only_once_confirmed(owner, voice_on, scripted):
    replies, _ = scripted
    events(owner.post("/app/intro/voice/start", json={}))
    replies.append("[next] Thank you. Who matters most to you?")
    say(owner)
    proposed = owner.post("/app/intro/voice/finish").json()
    assert [f["content"] for f in proposed["facts"]][0] == "Jordan leads applied AI in Singapore."
    assert intro_call_facts() == []  # nothing saved yet
    assert owner.get("/app/intro/voice").json()["proposed"] == proposed["facts"]

    edited = [
        {"content": "Jordan leads applied AI at PebbleRoad in Singapore.", "category": "work"},
        {"content": "Priya, Jordan's wife, can always get through.", "category": "nonsense"},
    ]
    r = owner.post("/app/intro/voice/confirm", json={"facts": edited})
    assert r.status_code == 200 and r.json()["saved"] == 2
    saved = intro_call_facts()
    assert {m.content for m in saved} == {e["content"] for e in edited}
    assert {m.category for m in saved} == {"work", "about"}
    # Private to the owner: only they may ever see these.
    assert all(m.allowed_kind == "persons" and m.min_trust == 2 for m in saved)
    # Confirming the same again saves nothing new (deduplicated).
    assert owner.post("/app/intro/voice/confirm", json={"facts": edited}).json()["saved"] == 0
    assert "saved 2 things" in owner.get("/app/messages").json()[-2]["text"]


def test_discarding_the_summary_saves_nothing(owner, voice_on):
    events(owner.post("/app/intro/voice/start", json={}))
    say(owner, audio=None, text="I'm a designer.")
    offline = owner.post("/app/intro/voice/finish").json()["facts"]
    assert offline and "I'm a designer." in offline[0]["content"]  # offline: their own words
    assert owner.post("/app/intro/voice/confirm", json={"facts": []}).json()["saved"] == 0
    assert intro_call_facts() == []


def test_confirm_refuses_oversized_input(owner, voice_on):
    big = [{"content": "x"}] * 41
    assert owner.post("/app/intro/voice/confirm", json={"facts": big}).status_code == 422
    assert owner.post("/app/intro/voice/confirm", json={"facts": [{"content": "y" * 501}]}).status_code == 422


# --- input checks --------------------------------------------------------------------------------


def test_audio_that_is_too_long_or_not_audio_is_refused(owner, voice_on):
    events(owner.post("/app/intro/voice/start", json={}))
    r = owner.post("/app/intro/voice/turn", files={"audio": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 415
    r = owner.post("/app/intro/voice/turn", files={"audio": ("a.wav", b"\0" * 2_600_000, "audio/wav")})
    assert r.status_code == 413


def test_voice_style_choice(owner, voice_on):
    assert owner.get("/app/intro/voice").json()["voice"] == "formal_female"
    assert owner.post("/app/intro/voice/settings", json={"voice": "formal_male"}).json()["voice"] == "formal_male"
    assert owner.post("/app/intro/voice/settings", json={"voice": "pirate"}).status_code == 422


# --- budget caps -----------------------------------------------------------------------------------


def deepgram_and_elevenlabs(monkeypatch, settings, seen: list[httpx.Request]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.deepgram.com":
            body = {
                "metadata": {"duration": 4.2},
                "results": {"channels": [{"alternatives": [{"transcript": "I run a design studio."}]}]},
            }
            return httpx.Response(200, json=body)
        return httpx.Response(200, content=b"ID3fake-mp3")

    monkeypatch.setattr(providers, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(settings, "voice_asr", "auto")
    monkeypatch.setattr(settings, "voice_tts", "auto")
    monkeypatch.setattr(settings, "deepgram_api_key", "dg_" + "a" * 37)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "el_" + "b" * 37)


def test_real_providers_are_metered_at_their_price(owner, voice_on, monkeypatch, settings):
    seen: list[httpx.Request] = []
    deepgram_and_elevenlabs(monkeypatch, settings, seen)
    cfg = owner.get("/app/intro/voice").json()
    assert (cfg["asr"], cfg["tts"], cfg["asr_paid_by"]) == ("deepgram", "elevenlabs", "owner")
    start = events(owner.post("/app/intro/voice/start", json={"lang": "en-GB"}))
    turn = say(owner)
    assert of(turn, "transcript")[0]["text"] == "I run a design studio."
    dg = [r for r in seen if r.url.path == "/v1/listen"][0]
    assert dg.headers["authorization"].startswith("Token dg_") and dg.url.params["model"] == "nova-3"
    el = [r for r in seen if r.url.host == "api.elevenlabs.io"][0]
    assert el.headers["xi-api-key"].startswith("el_") and providers.VOICES["elevenlabs"]["formal_female"] in el.url.path
    assert json.loads(el.content)["model_id"] == "eleven_flash_v2_5"
    with session_scope() as s:
        rows = s.scalars(select(SpendEntry).where(SpendEntry.purpose.in_(["voice_asr", "voice_tts"]))).all()
        asr = [r for r in rows if r.purpose == "voice_asr"]
        assert len(asr) == 1 and asr[0].status == "settled" and asr[0].cost_usd == asr_cost("deepgram", "nova-3", 4.2)
        tts = [r for r in rows if r.purpose == "voice_tts"]
        spoken = [d["text"] for d in of(start, "sentence") + of(turn, "sentence")]
        assert len(tts) == len(spoken)
        assert sum(r.cost_usd for r in tts) == sum(tts_cost("elevenlabs", "eleven_flash_v2_5", len(t)) for t in spoken)
    # Keys and audio never reach the browser.
    assert "dg_" not in json.dumps(cfg) and "el_" not in json.dumps(of(turn, "done"))


def test_the_voice_budget_stops_paid_speech_and_the_browser_takes_over(owner, voice_on, monkeypatch, settings):
    seen: list[httpx.Request] = []
    deepgram_and_elevenlabs(monkeypatch, settings, seen)
    monkeypatch.setattr(settings, "budget_daily_by_bucket", {"voice": 0.000001})
    start = events(owner.post("/app/intro/voice/start", json={}))
    # No paid voice: every sentence is marked for the browser's own voice instead.
    assert of(start, "sentence") and all(a.get("fallback") for a in of(start, "audio"))
    turn = say(owner)
    assert of(turn, "error") and not of(turn, "sentence")  # no paid speech-to-text either
    assert not [r for r in seen if r.url.host in ("api.elevenlabs.io",) or r.url.path == "/v1/listen"]
    with session_scope() as s:
        refused = s.scalars(select(AuditLog).where(AuditLog.action == "llm_call_refused")).all()
        assert {r.detail.get("purpose") for r in refused} >= {"voice_tts", "voice_asr"}


# --- providers, prices and credits -----------------------------------------------------------------


def test_choosing_providers_owner_key_then_credits_then_browser(monkeypatch, settings):
    from superaide.brain.wiring import credits_on  # noqa: F401  (the function choose_* consults)

    monkeypatch.setattr(settings, "deepgram_api_key", "")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    assert providers.choose_asr(settings).kind == "browser"
    assert providers.choose_tts(settings, "en-US").kind == "browser"
    monkeypatch.setattr(settings, "platform_deepgram_api_key", "platform-dg-key-123456789012345")
    monkeypatch.setattr("superaide.brain.wiring.credits_on", lambda s: True)
    asr = providers.choose_asr(settings)
    assert (asr.kind, asr.key_source) == ("deepgram", "platform")
    tts = providers.choose_tts(settings, "en-US")
    assert (tts.kind, tts.key_source) == ("deepgram", "platform")
    assert providers.choose_tts(settings, "hi-IN").kind == "browser"  # Aura-2 is English-only
    monkeypatch.setattr(settings, "elevenlabs_api_key", "el_" + "c" * 30)
    assert providers.choose_tts(settings, "hi-IN").kind == "elevenlabs"
    monkeypatch.setattr("superaide.brain.wiring.credits_on", lambda s: False)
    assert providers.choose_asr(settings).kind == "browser"


def test_platform_voice_keys_never_enter_an_owners_settings(monkeypatch):
    from superaide.vault.store import runtime_settings

    env = get_settings()
    monkeypatch.setattr(env, "platform_deepgram_api_key", "platform-secret-dg")
    monkeypatch.setattr(env, "platform_elevenlabs_api_key", "platform-secret-el")
    live = runtime_settings()
    assert live.platform_deepgram_api_key == "" and live.platform_elevenlabs_api_key == ""
    assert "platform-secret" not in live.model_dump_json()


def test_prices():
    assert asr_cost("deepgram", "nova-3", 0.2) == Decimal("0.000072")  # billed as one second
    assert tts_cost("elevenlabs", "eleven_flash_v2_5", 1000) == Decimal("0.04")
    assert per_minute(("deepgram", "nova-3"), ("elevenlabs", "eleven_flash_v2_5")) == Decimal("0.0202")
    with pytest.raises(KeyError):
        asr_cost("nobody", "x", 1)


def test_reply_reader_streams_whole_sentences_and_strips_the_tag():
    got: list[str] = []
    r = ReplyReader(got.append)
    for piece in ["[fol", "low] Twelve years at ", "PebbleRoad is a long time. What ", "keeps you there?"]:
        r.feed(piece)
    assert got == ["Twelve years at PebbleRoad is a long time."]
    r.close()
    assert r.tag == "follow" and got[-1] == "What keeps you there?" and "[" not in r.text
    untagged = ReplyReader(got.append)
    untagged.feed("Thanks. ")
    untagged.close()
    assert untagged.tag == "next"
    assert list(split_sentences("Hi. I'm Ava. Thank you for making the time today.")) == [
        "Hi. I'm Ava. Thank you for making the time today."
    ]


# --- connections: a Voice card ---------------------------------------------------------------------


@pytest.fixture
def vault_key(monkeypatch):
    from superaide.vault import crypto

    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"v" * 32).decode())
    monkeypatch.delenv("SECRETS_MASTER_KEY_FILE", raising=False)
    crypto.master_key.cache_clear()
    yield
    crypto.master_key.cache_clear()


def test_connecting_voice_keys_checks_them_and_shows_only_a_hint(owner, vault_key, monkeypatch):
    from superaide.app import connections

    def service(request: httpx.Request) -> httpx.Response:
        ok = request.headers.get("authorization", "").endswith("good" * 8) or request.headers.get(
            "xi-api-key", ""
        ).endswith("good" * 8)
        return httpx.Response(200 if ok else 401, json={})

    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(service)))
    bad = owner.post("/app/connections/deepgram", json={"api_key": "bad" * 10})
    assert bad.status_code == 400 and "didn't accept" in bad.json()["detail"]
    r = owner.post("/app/connections/deepgram", json={"api_key": "good" * 8})
    assert r.status_code == 200 and r.json()["services"]["deepgram"]["connected"]
    r = owner.post("/app/connections/elevenlabs", json={"api_key": "good" * 8})
    assert r.json()["services"]["elevenlabs"]["connected"] and "goodgoodgood" not in r.text
    assert "deepgram" in r.json()["guides"] and "elevenlabs" in r.json()["guides"]
    r = owner.post("/app/connections/deepgram/disconnect")
    assert not r.json()["services"]["deepgram"]["connected"]


# --- many owners: each call is its own --------------------------------------------------------------


def test_each_owners_call_is_their_own(owner, browser, voice_on):
    events(owner.post("/app/intro/voice/start", json={}))
    say(owner, audio=None, text="Project Bluebird is my secret.")
    second = browser()
    assert signup(second, **OTHER).status_code == 200 and hire(second).status_code == 200
    cfg = second.get("/app/intro/voice").json()
    assert not cfg["in_progress"]
    assert second.post("/app/intro/voice/finish").json()["facts"] == []
    first_turns = events(second.post("/app/intro/voice/start", json={}))
    assert "Priya" in of(first_turns, "sentence")[0]["text"] and "Bluebird" not in json.dumps(first_turns)
    assert owner.get("/app/intro/voice").json()["in_progress"]
