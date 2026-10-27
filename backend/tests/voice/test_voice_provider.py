"""Live voice (#125): a pluggable provider, a thin shell with no tools, and the app as the only second channel."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.approvals.models import Approval
from superaide.brain.turn import Prediction, RequestedAction, TurnOutput
from superaide.evals import voice_vs_text
from superaide.features.voice import gateway, provider
from superaide.features.voice.providers import VoiceError
from superaide.features.voice.shell import VoiceShell
from superaide.identity.models import ChannelIdentity, Person
from superaide.identity.resolver import Sender
from superaide.platform.db.session import session_scope
from superaide.policy.actions import ApprovalError, resolve


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "voice_provider", "fake")


def requester_for(manager):
    def make(s):
        person = s.get(Person, manager.id)
        identity = s.scalar(select(ChannelIdentity).where(ChannelIdentity.person_id == person.id))
        return Sender(identity=identity, person=person, trust=3)

    return make


def test_default_provider_without_a_key_is_none_and_refuses_to_open(settings):
    assert (
        settings.voice_provider == "auto" and settings.voice_provider_api_key == "" and settings.deepgram_api_key == ""
    )
    p = provider.choose(settings)
    assert p.name == "none"
    with pytest.raises(VoiceError):
        provider.run_call(p, lambda t: t)


def test_an_unknown_provider_name_fails_closed(monkeypatch, settings):
    monkeypatch.setattr(settings, "voice_provider", "typo")
    assert provider.choose(settings).name == "none"


def test_the_vendor_stub_spends_nothing_and_never_runs(monkeypatch, settings):
    monkeypatch.setattr(settings, "voice_provider", "realtime")
    p = provider.choose(settings)
    assert p.name == "realtime"
    with pytest.raises(VoiceError, match="API key"):
        p.open()
    monkeypatch.setattr(settings, "voice_provider_api_key", "k" * 20)
    with pytest.raises(VoiceError, match="No realtime voice vendor"):
        provider.choose(settings).open()


def test_the_fake_provider_replays_transcripts_and_records_speech(monkeypatch, settings):
    monkeypatch.setattr(settings, "voice_provider", "fake")
    p = provider.choose(settings, script=["hello", "bye"])
    said = provider.run_call(p, lambda t: t.upper())
    assert said == ["HELLO", "BYE"] and p.spoken == said and p.opened and p.closed


def scripted(kind, summary="Tell Priya the meeting moved", reply="Done, sent it!"):
    def understand(text):
        return TurnOutput(
            reply=reply,
            requested_action=RequestedAction(kind=kind, summary=summary),
            prediction=Prediction(expectation="x", confidence=0.5),
        )

    return understand


def test_a_call_proposal_waits_for_the_app_and_never_says_done(on, manager):
    shell = VoiceShell(scripted("message_someone_else"), requester_for(manager))
    p = provider.FakeVoiceProvider(script=["tell priya the meeting moved"])
    said = provider.run_call(p, shell.handle)
    assert "Nothing has happened yet" in said[0] and not gateway.DONE.search(said[0])
    assert shell.tools == ()
    with session_scope() as s:
        a = s.scalar(select(Approval))
        assert a.status == "pending" and a.payload["origin"] == "voice"


def test_a_plain_reply_that_claims_done_is_replaced_while_something_waits(on, manager):
    shell = VoiceShell(scripted("message_someone_else"), requester_for(manager))
    shell.handle("tell priya")
    shell.understand = scripted("none", reply="All set, that's sent.")
    said = shell.handle("anything new?")
    assert "Nothing has happened yet" in said


@pytest.mark.parametrize("via", ["telegram", "slack", "voice", "", "email"])
def test_only_a_tap_in_the_app_confirms_a_voice_approval(on, manager, via):
    shell = VoiceShell(scripted("message_someone_else"), requester_for(manager))
    shell.handle("tell priya")
    with session_scope() as s:
        a = s.scalar(select(Approval))
        with pytest.raises(ApprovalError) as e:
            resolve(s, a, approve=True, decided_by="Owner", via=via)
        assert e.value.status == 409 and a.status == "pending"
    with session_scope() as s:
        a = s.scalar(select(Approval))
        assert resolve(s, a, approve=True, decided_by="Owner", via="app").status == "approved"


def test_a_telegram_no_still_works(on, manager):
    shell = VoiceShell(scripted("message_someone_else"), requester_for(manager))
    shell.handle("tell priya")
    with session_scope() as s:
        assert resolve(s, s.scalar(select(Approval)), approve=False, decided_by="Owner", via="telegram").status == (
            "rejected"
        )


def test_the_thirty_task_experiment_runs_offline_and_reports_divergence(manager):
    report = voice_vs_text.run(manager.id)
    assert report["tasks"] == 30 == len(voice_vs_text.TASKS)
    assert report["diverged"] > 0 and 0 < report["divergence_rate"] < 1
    assert report["confirmation_rate"] == 1.0
    assert report["ran_without_app_tap"] == 0 and report["spoken_claims_of_done"] == 0
    assert report["wrong_action_rate"] > 0  # a mishearing becomes a wrong proposal, held for the app
    assert set(report["divergence_by_kind"]) <= {"dropped", "spurious", "wrong_kind", "wrong_details"}
    with session_scope() as s:
        assert all(a.status != "approved" for a in s.scalars(select(Approval)).all())
    assert "Capability parity" in voice_vs_text.render(report)


# --- voice by key (#448): no flag; a key turns it on, no key leaves it off with a hint ------------------------------


def test_without_a_key_voice_is_off_and_says_what_to_add(settings):
    assert provider.resolved_kind(settings) == "none" and not provider.voice_available(settings)
    assert "Deepgram key" in (provider.voice_hint(settings) or "")


def test_a_deepgram_key_turns_voice_on_and_the_hint_goes_away(monkeypatch, settings):
    monkeypatch.setattr(settings, "deepgram_api_key", "k" * 20)
    assert provider.resolved_kind(settings) == "deepgram" and provider.voice_available(settings)
    assert provider.voice_hint(settings) is None and provider.choose(settings).name == "deepgram"


def test_the_voice_provider_key_also_turns_it_on(monkeypatch, settings):
    monkeypatch.setattr(settings, "voice_provider_api_key", "k" * 20)
    assert provider.voice_available(settings)


def test_none_forces_voice_off_even_with_a_key(monkeypatch, settings):
    monkeypatch.setattr(settings, "deepgram_api_key", "k" * 20)
    monkeypatch.setattr(settings, "voice_provider", "none")
    assert not provider.voice_available(settings)


def test_a_vendor_name_without_a_key_is_off_and_never_builds_a_vendor_client(monkeypatch, settings):
    for kind in ("deepgram", "realtime"):
        monkeypatch.setattr(settings, "voice_provider", kind)
        assert not provider.voice_available(settings)
    assert provider.choose(settings).name == "realtime"  # the offline stub: it refuses to open without a key
    monkeypatch.setattr(settings, "voice_provider", "deepgram")
    assert provider.choose(settings).name == "none"


def test_a_whitespace_key_is_no_key(monkeypatch, settings):
    monkeypatch.setattr(settings, "deepgram_api_key", "   ")
    assert not provider.voice_available(settings)


def test_the_fake_provider_is_on_offline_and_needs_no_key(monkeypatch, settings):
    monkeypatch.setattr(settings, "voice_provider", "fake")
    assert provider.voice_available(settings) and provider.voice_hint(settings) is None


def test_without_a_key_a_voice_action_is_refused_with_the_hint_and_makes_no_approval(settings):
    with session_scope() as s:
        out = gateway.propose(
            s, requester=Sender(identity=None, person=None, trust=3), kind="message_someone_else", summary="x"
        )  # type: ignore[arg-type]
        assert out["status"] == "off" and "Deepgram key" in out["hint"]
        assert s.scalars(select(Approval)).all() == []


def test_the_example_env_has_no_voice_flag():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
    assert "VOICE_TWO_CHANNEL" not in text
