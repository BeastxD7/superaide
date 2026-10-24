"""Two-channel voice (#125): what is heard on a call is confirmed in text, never by voice."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.features.voice import gateway
from superaide.features.voice.call import VoiceCall
from superaide.identity.resolver import Sender
from superaide.platform.db.models import Approval, AuditLog
from superaide.platform.db.session import session_scope
from superaide.policy.actions import ApprovalError, decide, resolve


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "voice_two_channel", True)


@pytest.fixture
def owner_sender(manager):
    with session_scope() as s:
        from superaide.platform.db.models import ChannelIdentity, Person

        person = s.get(Person, manager.id)
        identity = s.scalar(select(ChannelIdentity).where(ChannelIdentity.person_id == person.id))
        return Sender(identity=identity, person=person, trust=3)


def propose(sender, kind="message_someone_else", summary="Tell Priya the meeting moved", **payload):
    with session_scope() as s:
        out = gateway.propose(s, requester=sender, kind=kind, summary=summary, payload=payload or None)
        return out


def approval(out) -> Approval:
    with session_scope() as s:
        a = s.get(Approval, __import__("uuid").UUID(out["approval_id"]))
        s.expunge(a)
        return a


def test_off_by_default_voice_proposes_nothing(owner_sender):
    out = propose(owner_sender)
    assert out["status"] == "off" and "approval_id" not in out
    with session_scope() as s:
        assert s.scalar(select(Approval)) is None


def test_a_call_proposal_waits_and_reads_back_the_exact_action(on, owner_sender):
    out = propose(owner_sender, to="Priya Tan", body="x" * 400)
    assert out["status"] == "waiting"
    assert out["kind"] == "message_someone_else" and out["summary"] == "Tell Priya the meeting moved"
    assert {"label": "to", "value": "Priya Tan"} in out["fields"]
    assert all(len(f["value"]) <= gateway.MAX_FIELD for f in out["fields"])
    assert "Nothing has happened yet" in out["spoken"] and "screen" in out["spoken"]
    assert not gateway.DONE.search(out["spoken"])
    a = approval(out)
    assert a.status == "pending" and a.payload["origin"] == "voice"


def test_a_spoken_yes_is_never_accepted_and_changes_nothing(on, owner_sender):
    a = approval(propose(owner_sender))
    with session_scope() as s:
        row = s.get(Approval, a.id)
        for via in ("voice", "", "phone", "unknown"):
            with pytest.raises(ApprovalError) as e:
                resolve(s, row, approve=True, decided_by="Owner", via=via)
            assert e.value.status == 409, via
        assert row.status == "pending" and row.decided_at is None
        assert s.scalar(select(AuditLog).where(AuditLog.action == "voice_confirmation_refused")) is not None


def test_a_tap_in_a_text_channel_confirms_and_a_no_works_from_anywhere(on, owner_sender):
    a = approval(propose(owner_sender))
    b = approval(propose(owner_sender, summary="Tell Ravi the deadline moved"))
    with session_scope() as s:
        assert resolve(s, s.get(Approval, a.id), approve=True, decided_by="Owner", via="app").status == "approved"
        assert resolve(s, s.get(Approval, b.id), approve=False, decided_by="Owner", via="voice").status == "rejected"


def test_a_spoken_yes_is_refused_even_for_an_approval_that_did_not_start_on_a_call(owner_sender):
    with session_scope() as s:
        d = decide(s, kind="message_someone_else", summary="Tell Priya", requester=owner_sender)
        with pytest.raises(ApprovalError):
            resolve(s, d.approval, approve=True, decided_by="Owner", via="voice")


def test_ordinary_approvals_are_unchanged(owner_sender):
    with session_scope() as s:
        d = decide(s, kind="message_someone_else", summary="Tell Priya", requester=owner_sender)
        assert "origin" not in d.approval.payload
        assert resolve(s, d.approval, approve=True, decided_by="Owner").status == "approved"


def test_a_reply_that_claims_it_is_done_is_replaced_while_the_action_waits(on, owner_sender):
    a = approval(propose(owner_sender))
    for claim in ("Done, I've sent it.", "All set!", "That's booked.", "The message has been sent to Priya."):
        said = gateway.guard_spoken(claim, [a])
        assert said != claim and "Nothing has happened yet" in said, claim
    assert gateway.guard_spoken("Anything else I can help with?", [a]) == "Anything else I can help with?"
    assert gateway.guard_spoken("Done.", []) == "Done."  # nothing is waiting: not this guard's business


def test_the_voice_shell_holds_no_tools(settings, agent, monkeypatch):
    assert gateway.VOICE_TOOLS == ()
    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda s: True)
    VoiceCall(agent)._reply([{"role": "owner", "text": "hello"}], "Jordan", 0, 0, "en-US", lambda s: None)
    assert agent.llm.calls and all(c["tools"] == [] for c in agent.llm.calls)


def test_a_spoken_request_nothing_can_carry_out_creates_no_card_and_does_not_pretend(
    on, owner_sender, monkeypatch, settings
):
    # #326 for voice: "tell Priya..." has no executor, so no approval card is made and the call is told so.
    monkeypatch.setattr(settings, "approval_require_executor", True)
    out = propose(owner_sender)
    assert out["status"] == "refused" and "can't do that myself" in out["spoken"]
    assert "waiting" not in out["spoken"].lower() and "screen" not in out["spoken"].lower()
    with session_scope() as s:
        assert s.scalars(select(Approval)).all() == []
        refused = s.scalars(select(AuditLog).where(AuditLog.action == "action_refused")).all()
        assert refused and "no executor" in str(refused[0].detail)


def test_with_the_executor_check_off_a_spoken_request_still_waits_for_a_tap(on, owner_sender, monkeypatch, settings):
    monkeypatch.setattr(settings, "approval_require_executor", False)
    out = propose(owner_sender)
    assert out["status"] == "waiting" and approval(out).payload["origin"] == "voice"
