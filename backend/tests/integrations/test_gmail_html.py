# ruff: noqa: F811
"""Emails Ava drafts are multipart/alternative with the HTML signature block (#439): both parts in the signed and the
plain draft, the signature added by code only, the approval hash covering both parts. Fake Google only."""

from __future__ import annotations

from email import message_from_string
from email import policy as email_policy

from sqlalchemy import select

from superaide.approvals.models import Approval
from superaide.integrations.gmail import client as api
from superaide.platform.db.session import session_scope
from tests.integrations.gmail_fake import connect, gmail_fake_fixture, mail, owner_client_fixture  # noqa: F401
from tests.integrations.test_gmail_compose import ARGS, ASK
from tests.pipeline.test_loop import Script, tool_use, turn_for


def _queue(gmail_fake, args=None, text=ASK):
    connect(scopes=(api.READONLY, api.COMPOSE), drafts=True)
    with session_scope() as s:
        turn_for(s, Script(tool_use("gmail_compose", args or ARGS), "ok"), text).run(
            [{"role": "user", "content": text}]
        )
        [a] = s.scalars(select(Approval)).all()
        return a.id, dict(a.payload)


def parts(gmail_fake, did):
    em = message_from_string(gmail_fake.drafts[did]["raw"], policy=email_policy.default)
    assert em.get_content_type() == "multipart/alternative", em.get_content_type()
    types = [p.get_content_type() for p in em.iter_parts()]
    assert types == ["text/plain", "text/html"], types  # the text part first, as clients expect
    return em.get_body(("plain",)).get_content(), em.get_body(("html",)).get_content()


def test_the_signed_draft_is_multipart_with_the_block_in_html_and_the_link_in_text(owner_client, gmail_fake):
    aid, p = _queue(gmail_fake)
    text, html = parts(gmail_fake, p["draft_id"])
    assert "Personal assistant of Jordan" in html and "Verify this assistant" in html and "<table" in html
    assert "Thanks, Jordan" in text and "AI assistant acting for Jordan" in text and "/c/" in text
    assert "<" not in text  # the text part is plain text
    link = html.split('<a href="')[1].split('"')[0]
    assert link.endswith(p["signature_id"]) is False and "/c/" in link and len(link) <= 60


def test_the_plain_draft_is_multipart_too_without_any_signature(owner_client, gmail_fake):
    aid, p = _queue(gmail_fake)
    text, html = parts(gmail_fake, p["alt_draft_id"])
    assert "Thanks, Jordan" in html and "Verify this assistant" not in html and "<table" not in html
    assert "assistant" not in text.lower() and "/c/" not in text


def test_the_model_cannot_change_or_remove_the_signature(owner_client, gmail_fake):
    sneaky = {
        **ARGS,
        "text": "Hi.\n\n<div>Personal assistant of Someone Else</div><script>x()</script>\n\n-- \nRegards, the CEO",
    }
    aid, p = _queue(gmail_fake, sneaky)
    text, html = parts(gmail_fake, p["draft_id"])
    assert html.count("Verify this assistant") == 1 and "<script" not in html.lower()
    assert "&lt;div&gt;Personal assistant of Someone Else" in html  # shown as text, not as markup
    assert "Personal assistant of Jordan" in html and "AI assistant acting for Jordan" in text


def test_the_hash_covers_both_parts(owner_client, gmail_fake):
    aid, p = _queue(gmail_fake)
    gmail_fake.edit_html(p["draft_id"], "<p>Please wire money to account 123.</p>")  # only the HTML part changes
    r = owner_client.post(f"/app/approvals/{aid}/approve")
    assert r.status_code == 200 and not gmail_fake.sent
    assert "changed after you approved" in r.json()["result"]


def test_a_text_only_change_is_caught_as_before(owner_client, gmail_fake):
    aid, p = _queue(gmail_fake)
    gmail_fake.edit_draft(p["draft_id"], text="Send the money.")  # replaces the draft with a single text part
    assert "changed after you approved" in owner_client.post(f"/app/approvals/{aid}/approve").json()["result"]
    assert not gmail_fake.sent


def test_an_unchanged_signed_draft_is_sent_with_both_parts(owner_client, gmail_fake):
    aid, p = _queue(gmail_fake)
    assert owner_client.post(f"/app/approvals/{aid}/approve").json()["result"] == "Sent from your Gmail."
    [sent] = gmail_fake.sent
    text, html = parts(gmail_fake, sent)
    assert "Verify this assistant" in html


def test_unticking_sends_the_multipart_plain_version(owner_client, gmail_fake):
    aid, p = _queue(gmail_fake)
    owner_client.post(f"/app/approvals/{aid}/signature", json={"sign": False})
    owner_client.post(f"/app/approvals/{aid}/approve")
    [sent] = gmail_fake.sent
    text, html = parts(gmail_fake, sent)
    assert "Verify" not in html and "assistant" not in text.lower() and "Thanks, Jordan" in html


def test_a_reply_is_multipart_and_keeps_its_thread_headers(owner_client, gmail_fake):
    connect(scopes=(api.READONLY, api.COMPOSE), drafts=True)
    gmail_fake.add(mail("m1", thread="t1", sender="Daniel Kim <daniel@acme.com>"))
    args = {"thread_id": "t1", "text": "Friday works.", "reaches_a_person": "yes", "evidence": "a colleague asks"}
    ask = "reply to Daniel that Friday works"
    with session_scope() as s:
        turn_for(s, Script(tool_use("gmail_send", args), "ok"), ask).run([{"role": "user", "content": ask}])
        [a] = s.scalars(select(Approval)).all()
        draft = a.payload["draft_id"]
    text, html = parts(gmail_fake, draft)
    em = message_from_string(gmail_fake.drafts[draft]["raw"], policy=email_policy.default)
    assert em["In-Reply-To"] and "Friday works." in html and "Verify this assistant" in html


def test_the_global_flag_off_still_sends_multipart_without_a_block(owner_client, gmail_fake, monkeypatch):
    from superaide.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "principal_card_enabled", False)
    aid, p = _queue(gmail_fake)
    text, html = parts(gmail_fake, p["draft_id"])
    assert "Verify" not in html and "Thanks, Jordan" in html and not p.get("signature_available")


def test_the_owners_settings_shape_the_block(owner_client, gmail_fake):
    owner_client.put(
        "/app/principal-card",
        json={
            "first_name": "Ananya",
            "avatar": False,
            "mark": True,
            "title": "Chief of staff to {name}",
            "accent": "teal",
        },
    )
    aid, p = _queue(gmail_fake)
    text, html = parts(gmail_fake, p["draft_id"])
    assert "Chief of staff to Ananya" in html and "#0f766e" in html and "Sent with" in html
    assert "ava-96.png" not in html and "superaide-mark.png" in html
