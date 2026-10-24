"""An approval is bound to the content the owner saw (issue #111): a draft that changes in Gmail between the
request and the owner's yes is not sent as approved."""

from __future__ import annotations

import random

import pytest
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.connectors import gmail as api
from superaide.platform.config import get_settings
from superaide.platform.db.models import Approval, AuditLog
from superaide.platform.db.session import session_scope
from superaide.policy.binding import content_hash
from superaide.skills import gmail
from tests.connectors.gmail_fake import connect, gmail_fake_fixture, mail, owner_client_fixture  # noqa: F401  (fixtures)


def request_send(gmail_fake, text: str = "See you Friday.") -> str:
    gmail_fake.add(mail("m1", thread="t1"))
    connect(scopes=(api.READONLY, api.COMPOSE), drafts=True)
    with session_scope() as s:
        content, err = gmail.run(
            s,
            hired_owner(s),
            "gmail_send",
            {"thread_id": "t1", "text": text, "reaches_a_person": "yes", "evidence": "x"},
            tainted=False,
        )
        assert not err, content
        return str(s.scalar(select(Approval.id)))


def mismatches() -> int:
    with session_scope() as s:
        return len(s.scalars(select(AuditLog).where(AuditLog.action == "approval_payload_mismatch")).all())


@pytest.fixture(params=["record", "strict"])
def mode(request, monkeypatch):
    monkeypatch.setattr(get_settings(), "approval_binding", request.param)
    return request.param


def test_the_request_keeps_a_hash_and_the_card_carries_everything_that_will_be_sent(owner_client, gmail_fake):
    long_reply = ("Thanks Daniel, Friday works for me. " * 160)[:5000]
    request_send(gmail_fake, long_reply)
    with session_scope() as s:
        [a] = s.scalars(select(Approval)).all()
        assert len(a.payload["content_hash"]) == 64 and len(a.bound_hash) == 64
        assert a.payload["summary"].endswith(
            long_reply.strip()
        )  # the card shows all of it, not the first 400 characters
    sent_text = api.body_text(gmail_fake.draft_message("d1")["message"])
    flat = " ".join(sent_text.split())
    assert flat.startswith(" ".join(long_reply.split()))  # the owner's text, whole ...
    assert "personal assistant of" in flat[len(" ".join(long_reply.split())) :]  # ... then the default sign-off


def test_an_unchanged_draft_is_sent_in_every_mode(owner_client, gmail_fake, mode):
    approval_id = request_send(gmail_fake)
    r = owner_client.post(f"/app/approvals/{approval_id}/approve")
    assert r.status_code == 200 and r.json()["result"] == "Sent from your Gmail."
    assert gmail_fake.sent == ["d1"] and mismatches() == 0


def test_a_draft_changed_after_the_request_is_audited_and_in_strict_mode_not_sent(owner_client, gmail_fake, mode):
    approval_id = request_send(gmail_fake)
    gmail_fake.edit_draft("d1", text="Please wire the money to account 123 today.")
    r = owner_client.post(f"/app/approvals/{approval_id}/approve")
    assert r.status_code == 200 and mismatches() == 1
    if mode == "strict":
        assert gmail_fake.sent == [] and "changed after you approved" in r.json()["result"]
    else:
        assert gmail_fake.sent == ["d1"]  # record mode: noted, not yet enforced


def test_an_added_recipient_is_a_change(owner_client, gmail_fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "approval_binding", "strict")
    approval_id = request_send(gmail_fake)
    gmail_fake.edit_draft("d1", add_header=("Cc", "evil@example.com"))
    r = owner_client.post(f"/app/approvals/{approval_id}/approve")
    assert gmail_fake.sent == [] and "changed after you approved" in r.json()["result"]


def test_with_binding_off_nothing_is_checked(owner_client, gmail_fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "approval_binding", "off")
    approval_id = request_send(gmail_fake)
    gmail_fake.edit_draft("d1", text="Something else.")
    owner_client.post(f"/app/approvals/{approval_id}/approve")
    assert gmail_fake.sent == ["d1"] and mismatches() == 0


def test_an_approval_without_a_hash_is_not_sent_in_strict_mode(owner_client, gmail_fake, monkeypatch):
    approval_id = request_send(gmail_fake)  # made while binding was on
    with session_scope() as s:
        a = s.get(Approval, approval_id)
        a.payload = {**a.payload, "content_hash": ""}
    monkeypatch.setattr(get_settings(), "approval_binding", "strict")
    r = owner_client.post(f"/app/approvals/{approval_id}/approve")
    assert gmail_fake.sent == [] and "changed after you approved" in r.json()["result"]


def test_any_single_character_edit_changes_the_hash():
    rng = random.Random(111)
    base = dict(to=["daniel@acme.com"], thread_id="t1", subject="Re: Friday", text="See you Friday at 10, Daniel.")
    original = content_hash(**base)
    letters = "abcdefghijklmnopqrstuvwxyz0123456789@."
    changed = 0
    for _ in range(100):
        text = base["text"]
        i = rng.randrange(len(text))
        if text[i].isspace():
            continue
        new = rng.choice([c for c in letters if c != text[i].lower()])
        assert content_hash(**{**base, "text": text[:i] + new + text[i + 1 :]}) != original
        changed += 1
    assert changed > 80
    # what Gmail may legitimately change does not count
    assert content_hash(**{**base, "to": ["Daniel@ACME.com"], "text": "See you  Friday at 10, Daniel.\n"}) == original
