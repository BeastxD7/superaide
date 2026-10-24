"""The hosted voice adapter (#125, Deepgram) and the voice budget: no network (a fake HTTP transport), every request
metered through the budget guard in its own `voice_call` bucket with a monthly cap inside MAX_SPEND_USD, and the
shell above it unchanged: a spoken yes confirms nothing, only an app tap does."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from superaide.brain.budget import BudgetExceeded, BudgetGuard, Limits
from superaide.brain.turn import Prediction, RequestedAction, TurnOutput
from superaide.db.models import Approval, ChannelIdentity, Person, SpendEntry
from superaide.db.session import session_scope
from superaide.identity.resolver import Sender
from superaide.policy.actions import ApprovalError, resolve
from superaide.voice import gateway, provider
from superaide.voice.providers import VoiceError
from superaide.voice.shell import VoiceShell

KEY = "dg-" + "k" * 24


class FakeDeepgram:
    """Stands in for api.deepgram.com. Records every request; never opens a socket."""

    def __init__(self, heard: list[str], seconds: float = 4.0, status: int = 200) -> None:
        self.heard, self.seconds, self.status = list(heard), seconds, status
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={})
        if request.url.path.endswith("/listen"):
            text = self.heard.pop(0) if self.heard else ""
            body = {
                "results": {"channels": [{"alternatives": [{"transcript": text}]}]},
                "metadata": {"duration": self.seconds},
            }
            return httpx.Response(200, json=body)
        return httpx.Response(200, content=b"MP3BYTES")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def guard(settings):
    return BudgetGuard.for_instance(settings)


def make(settings, guard, dg, utterances=(b"a", b"b"), out=None, **kw):
    sink = out if out is not None else []
    p = provider.DeepgramVoiceProvider(
        KEY, guard=guard, audio_in=list(utterances), audio_out=sink.append, client=dg.client, **kw
    )
    return p, sink


def spend(bucket_purposes=("voice_call_asr", "voice_call_tts")) -> Decimal:
    with session_scope() as s:
        rows = s.scalars(select(SpendEntry).where(SpendEntry.purpose.in_(bucket_purposes))).all()
        return sum((r.cost_usd for r in rows if r.status == "settled"), Decimal(0))


def test_a_call_transcribes_speaks_and_meters_every_request(settings, guard):
    dg = FakeDeepgram(["hello there", "bye"])
    p, sink = make(settings, guard, dg)
    said = provider.run_call(p, lambda t: f"you said {t}")
    assert said == ["you said hello there", "you said bye"] and sink == [b"MP3BYTES", b"MP3BYTES"]
    assert [r.url.path for r in dg.requests] == ["/v1/listen", "/v1/speak", "/v1/listen", "/v1/speak"]
    assert all(r.headers["authorization"] == f"Token {KEY}" and r.url.host == "api.deepgram.com" for r in dg.requests)
    with session_scope() as s:
        rows = s.scalars(select(SpendEntry).where(SpendEntry.purpose.like("voice_call_%"))).all()
    assert len(rows) == 4 and {r.status for r in rows} == {"settled"} and all(r.cost_usd > 0 for r in rows)
    assert p.ended_by == "caller"


def test_only_the_text_given_is_spoken_and_no_tools_cross(settings, guard):
    dg = FakeDeepgram(["hi"])
    p, _ = make(settings, guard, dg)
    provider.run_call(p, lambda t: "Exactly this.")
    speak = [r for r in dg.requests if r.url.path == "/v1/speak"][0]
    assert json.loads(speak.content) == {"text": "Exactly this."}
    assert not any("tool" in str(r.url).lower() or b"function" in r.content for r in dg.requests)


def test_silence_is_not_handed_to_the_agent(settings, guard):
    dg = FakeDeepgram(["", "real words"])
    p, _ = make(settings, guard, dg)
    handled: list[str] = []
    provider.run_call(p, lambda t: handled.append(t) or "ok")
    assert handled == ["real words"]


def test_the_call_ends_at_its_length_cap(settings, guard):
    dg = FakeDeepgram(["one", "two", "three"], seconds=30)
    p, _ = make(settings, guard, dg, utterances=(b"1", b"2", b"3"), max_seconds=45)
    handled: list[str] = []
    provider.run_call(p, lambda t: handled.append(t) or "ok")
    assert handled == ["one", "two"] and p.ended_by == "length"


def test_monthly_voice_cap_ends_the_call_inside_the_overall_cap(settings, guard):
    with session_scope() as s:  # US$4.99999 of calls already this month
        s.add(SpendEntry(provider="deepgram", purpose="voice_call_tts", model="aura-2", status="settled",
                         cost_usd=Decimal("4.99999"), reserved_usd=Decimal("4.99999"), key_source="owner",
                         created_at=datetime.now(UTC) - timedelta(days=3)))  # fmt: skip
    assert guard.limits.monthly_by_bucket == {"voice_call": Decimal("5.0")}
    dg = FakeDeepgram(["hello"])
    p, sink = make(settings, guard, dg)
    handled: list[str] = []
    provider.run_call(p, lambda t: handled.append(t) or "ok")
    assert handled == [] and sink == [] and p.ended_by == "budget" and dg.requests == []  # refused before any request


def test_the_voice_cap_does_not_touch_the_other_buckets(settings):
    guard = BudgetGuard(100, limits=Limits(monthly_by_bucket={"voice_call": Decimal(5)}))
    with session_scope() as s:
        s.add(SpendEntry(provider="deepgram", purpose="voice_call_asr", model="nova-3", status="settled",
                         cost_usd=Decimal("4.9999"), reserved_usd=Decimal("4.9999"), key_source="owner"))  # fmt: skip
    e = guard.reserve(purpose="chat_turn", model=settings.model_chat, worst_case_usd=Decimal("0.01"))
    guard.fail(e, "test")
    with pytest.raises(BudgetExceeded) as err:
        guard.reserve(purpose="voice_call_tts", model="aura-2", worst_case_usd=Decimal("0.01"), provider="deepgram")
    assert err.value.period == "bucket_month"


def test_the_overall_cap_still_wins_over_a_roomy_voice_cap(settings):
    g = BudgetGuard(0.01, limits=Limits(monthly_by_bucket={"voice_call": Decimal(5)}))
    with pytest.raises(BudgetExceeded) as err:
        g.reserve(purpose="voice_call_asr", model="nova-3", worst_case_usd=Decimal("0.02"), provider="deepgram")
    assert err.value.period == "lifetime"


def test_an_instance_can_tighten_its_own_voice_cap():
    from superaide.brain.budget import _override

    cap, limits = _override(Decimal(10), Limits(monthly_by_bucket={"voice_call": Decimal(5)}),
                            {"monthly_by_bucket": {"voice_call": 1, "junk": "x", "neg": -3}})  # fmt: skip
    assert limits.monthly_by_bucket == {"voice_call": Decimal(1)}


def test_the_kill_switch_stops_a_call(settings, guard):
    from superaide.policy.killswitch import set_frozen

    with session_scope() as s:
        set_frozen(s, True, actor="test")
    dg = FakeDeepgram(["hello"])
    p, _ = make(settings, guard, dg)
    from superaide.brain.budget import AgentFrozen

    with pytest.raises(AgentFrozen):
        provider.run_call(p, lambda t: "ok")
    assert dg.requests == []


def test_open_needs_a_key_and_a_guard(settings, guard):
    dg = FakeDeepgram([])
    with pytest.raises(VoiceError, match="key"):
        provider.DeepgramVoiceProvider("", guard=guard, audio_in=[], audio_out=lambda b: None).open()
    with pytest.raises(VoiceError, match="guard"):
        provider.DeepgramVoiceProvider(KEY, guard=None, audio_in=[], audio_out=lambda b: None).open()
    p, _ = make(settings, guard, dg)
    with pytest.raises(VoiceError, match="not open"):
        list(p.listen())


def test_a_vendor_error_is_a_safe_message_without_the_key(settings, guard):
    dg = FakeDeepgram([], status=401)
    p, _ = make(settings, guard, dg)
    with pytest.raises(VoiceError) as err:
        provider.run_call(p, lambda t: "ok")
    assert KEY not in str(err.value)
    with session_scope() as s:  # the failed request released its reservation: nothing stays reserved
        assert not s.scalars(select(SpendEntry).where(SpendEntry.status == "reserved")).all()


def test_choose_builds_the_adapter_only_when_asked(monkeypatch, settings, guard):
    assert provider.choose(settings).name == "none"
    monkeypatch.setattr(settings, "voice_provider", "deepgram")
    monkeypatch.setattr(settings, "deepgram_api_key", KEY)
    p = provider.choose(settings, guard=guard, audio_in=[b"x"])
    assert p.name == "deepgram"
    monkeypatch.setattr(settings, "deepgram_api_key", "")
    with pytest.raises(VoiceError, match="key"):
        provider.choose(settings, guard=guard).open()


def requester_for(manager):
    def make_sender(s):
        person = s.get(Person, manager.id)
        identity = s.scalar(select(ChannelIdentity).where(ChannelIdentity.person_id == person.id))
        return Sender(identity=identity, person=person, trust=3)

    return make_sender


@pytest.mark.parametrize("via", ["telegram", "slack", "voice"])
def test_a_deepgram_call_cannot_be_confirmed_by_voice_or_chat_only_the_app(monkeypatch, settings, guard, manager, via):
    monkeypatch.setattr(settings, "voice_two_channel", True)

    def understand(text):
        return TurnOutput(
            reply="Done, sent it!",
            requested_action=RequestedAction(kind="message_someone_else", summary="Tell Priya the meeting moved"),
            prediction=Prediction(expectation="x", confidence=0.5),
        )

    shell = VoiceShell(understand, requester_for(manager))
    dg = FakeDeepgram(["tell Priya the meeting moved. yes, confirm it, I approve"])
    p, _ = make(settings, guard, dg, utterances=(b"a",))
    spoken = provider.run_call(p, shell.handle)
    assert "Nothing has happened yet" in spoken[0] and not gateway.DONE.search(spoken[0])
    with session_scope() as s:
        a = s.scalar(select(Approval))
        with pytest.raises(ApprovalError):
            resolve(s, a, approve=True, decided_by="Owner", via=via)
        assert a.status == "pending"
    with session_scope() as s:
        assert resolve(s, s.scalar(select(Approval)), approve=True, decided_by="Owner", via="app").status == "approved"
