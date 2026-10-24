# ruff: noqa: F811
"""Every email Ava sends is signed by default (#135): the sign-off is in the draft that is sent, the owner's tick on
the approval card swaps it out before the yes, the choice is part of the sealed approval, and Telegram and Slack
are untouched. Fake Google only; nothing leaves the machine."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.connectors import gmail as api
from superaide.platform.config import get_settings
from superaide.platform.db.models import Approval
from superaide.platform.db.session import session_scope
from superaide.policy import binding
from superaide.principal import card
from tests.connectors.gmail_fake import connect, gmail_fake_fixture, owner_client_fixture  # noqa: F401
from tests.connectors.test_gmail_compose import ARGS, ASK
from tests.pipeline.test_loop import Script, tool_use, turn_for

LINE = "Ava, personal assistant of Jordan"
HONEST = "is an AI assistant acting for Jordan"


def _queue(gmail_fake, args=None, text=ASK):
    connect(scopes=(api.READONLY, api.COMPOSE), drafts=True)
    with session_scope() as s:
        script = Script(tool_use("gmail_compose", args or ARGS), "It's waiting for your OK.")
        turn_for(s, script, text).run([{"role": "user", "content": text}])
        [a] = s.scalars(select(Approval)).all()
        return a.id


def body_of(gmail_fake, did):
    return gmail_fake.draft_message(did)  # parsed draft; the text is in its payload


def raw(gmail_fake, did):
    return gmail_fake.drafts[did]["raw"]


def card_of(owner_client):
    [c] = owner_client.get("/app/approvals").json()
    return c


def test_the_default_draft_carries_the_sign_off_and_the_card_says_so(owner_client, gmail_fake):
    aid = _queue(gmail_fake)
    with session_scope() as s:
        a = s.get(Approval, aid)
        p = a.payload
        assert p["sign_as_ava"] is True and p["signature_available"] is True
        sent_draft = p["draft_id"]
    text = raw(gmail_fake, sent_draft)
    assert LINE in text and HONEST in text and "/c/" in text
    c = card_of(owner_client)
    assert c["sign"]["checked"] is True and c["sign"]["label"] == f"Sign as {LINE}" and LINE in c["sign"]["text"]
    assert c["sign"]["can_change"] is True


def test_approving_sends_the_signed_draft_and_discards_the_other(owner_client, gmail_fake):
    aid = _queue(gmail_fake)
    assert owner_client.post(f"/app/approvals/{aid}/approve").status_code == 200
    [sent_id] = gmail_fake.sent
    assert LINE in raw(gmail_fake, sent_id)
    assert len(gmail_fake.drafts) == 1  # the unsigned variant was deleted
    with session_scope() as s:
        assert card.stats(s)["shown"] == 1
        assert [e["channel"] for e in card.sent(s)] == ["gmail"]


def test_unticking_sends_the_email_in_the_owners_own_name_with_no_ava_line(owner_client, gmail_fake):
    aid = _queue(gmail_fake)
    r = owner_client.post(f"/app/approvals/{aid}/signature", json={"sign": False})
    assert r.status_code == 200 and r.json()["checked"] is False and r.json()["text"] == ""
    assert owner_client.post(f"/app/approvals/{aid}/approve").status_code == 200
    [sent_id] = gmail_fake.sent
    text = raw(gmail_fake, sent_id)
    assert "Ava" not in text and "AI assistant" not in text and "/c/" not in text and "Thanks, Jordan" in text
    with session_scope() as s:
        assert card.stats(s)["shown"] == 0 and card.sent(s) == []  # nothing was shown, so nothing is counted


def test_the_tick_can_go_back_and_forth_before_the_yes(owner_client, gmail_fake):
    aid = _queue(gmail_fake)
    for want in (False, True, False, True):
        assert owner_client.post(f"/app/approvals/{aid}/signature", json={"sign": want}).json()["checked"] is want
    owner_client.post(f"/app/approvals/{aid}/approve")
    assert LINE in raw(gmail_fake, gmail_fake.sent[0])


def test_the_choice_is_part_of_the_sealed_hash(owner_client, gmail_fake):
    aid = _queue(gmail_fake)
    with session_scope() as s:
        a = s.get(Approval, aid)
        before = a.bound_hash
        assert before == binding.payload_hash(a.kind, a.payload)
    owner_client.post(f"/app/approvals/{aid}/signature", json={"sign": False})
    with session_scope() as s:
        a = s.get(Approval, aid)
        assert a.bound_hash != before and binding.check(a) == "ok"
        assert a.payload["sign_as_ava"] is False
        # flipping the stored choice behind the API breaks the seal
        a.payload = {**a.payload, "sign_as_ava": True}
        assert binding.check(a) == "mismatch"


def test_a_choice_flipped_after_the_yes_cannot_send_the_other_draft(owner_client, gmail_fake):
    aid = _queue(gmail_fake)
    owner_client.post(f"/app/approvals/{aid}/signature", json={"sign": False})
    with session_scope() as s:
        a = s.get(Approval, aid)
        a.payload = {**a.payload, "draft_id": a.payload["alt_draft_id"], "sign_as_ava": True}  # tampering
    r = owner_client.post(f"/app/approvals/{aid}/approve")
    assert r.status_code >= 400 or not gmail_fake.sent  # the seal no longer matches: nothing is sent
    assert not any(LINE in raw(gmail_fake, d) for d in gmail_fake.sent if d in gmail_fake.drafts)


def test_the_choice_is_refused_once_decided(owner_client, gmail_fake):
    aid = _queue(gmail_fake)
    owner_client.post(f"/app/approvals/{aid}/approve")
    r = owner_client.post(f"/app/approvals/{aid}/signature", json={"sign": False})
    assert r.status_code == 409
    c = owner_client.get("/app/approvals").json()[0]
    assert c["sign"]["can_change"] is False


def test_reject_deletes_both_drafts(owner_client, gmail_fake):
    aid = _queue(gmail_fake)
    assert len(gmail_fake.drafts) == 2
    owner_client.post(f"/app/approvals/{aid}/reject")
    assert gmail_fake.drafts == {} and not gmail_fake.sent


def test_the_settings_default_off_means_the_card_starts_unticked(owner_client, gmail_fake):
    owner_client.post("/app/principal-card/sign-default", json={"on": False})
    aid = _queue(gmail_fake)
    c = card_of(owner_client)
    assert c["sign"]["checked"] is False and c["sign"]["text"] == ""
    owner_client.post(f"/app/approvals/{aid}/signature", json={"sign": True})  # the tick still works per email
    owner_client.post(f"/app/approvals/{aid}/approve")
    assert LINE in raw(gmail_fake, gmail_fake.sent[0])


def test_the_global_flag_off_means_no_line_no_tick_and_one_draft(owner_client, gmail_fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "principal_card_enabled", False)
    aid = _queue(gmail_fake)
    assert "sign" not in card_of(owner_client) and len(gmail_fake.drafts) == 1
    owner_client.post(f"/app/approvals/{aid}/approve")
    assert "Ava" not in raw(gmail_fake, gmail_fake.sent[0])
    assert owner_client.post(f"/app/approvals/{aid}/signature", json={"sign": True}).status_code in (404, 409)


def test_the_owners_chosen_name_and_wording_are_used(owner_client, gmail_fake):
    owner_client.put(
        "/app/principal-card", json={"first_name": "Ananya", "initial": "T", "wording": "Warmly,\n{agent} for {name}"}
    )
    _queue(gmail_fake)
    text = raw(gmail_fake, "d1")
    assert (
        "Warmly,\nAva for Ananya T." in text and "acting for Ananya T." in text and "Jordan" not in text.split("--")[-1]
    )


def test_a_reply_draft_is_signed_the_same_way(owner_client, gmail_fake):
    from tests.connectors.gmail_fake import mail

    connect(scopes=(api.READONLY, api.COMPOSE), drafts=True)
    gmail_fake.add(mail("m1", thread="t1", sender="Daniel Kim <daniel@acme.com>"))
    args = {"thread_id": "t1", "text": "Friday works.", "reaches_a_person": "yes", "evidence": "a colleague asks"}
    ask = "reply to Daniel that Friday works"
    with session_scope() as s:
        turn_for(s, Script(tool_use("gmail_send", args), "Waiting."), ask).run([{"role": "user", "content": ask}])
        [a] = s.scalars(select(Approval)).all()
        assert a.payload["sign_as_ava"] is True and a.payload["alt_draft_id"]
        draft = a.payload["draft_id"]
    assert LINE in raw(gmail_fake, draft) and "In-Reply-To" in raw(gmail_fake, draft)
    assert LINE not in raw(gmail_fake, "d2")


@pytest.mark.parametrize("path", ["/app/approvals/{}/signature"])
def test_signature_route_is_owner_only(owner_client, gmail_fake, agent, path):
    from fastapi.testclient import TestClient

    from superaide.main import create_app

    aid = _queue(gmail_fake)
    anon = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    assert anon.post(path.format(aid), json={"sign": False}).status_code in (401, 403)
    assert (
        owner_client.post(path.format("00000000-0000-0000-0000-000000000000"), json={"sign": False}).status_code == 404
    )
    assert owner_client.post(path.format(aid), json={"sign": "maybe"}).status_code == 422
