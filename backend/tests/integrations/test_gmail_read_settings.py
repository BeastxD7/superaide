# ruff: noqa: F811
"""#433: what Ava may read in Gmail is the owner's own setting, enforced in the tool results, never hidden."""

from __future__ import annotations

import pytest

from superaide.integrations.gmail import client as api
from superaide.integrations.gmail import settings as gmail_settings
from superaide.integrations.gmail import skill as gmail
from superaide.platform.db.models import AuditLog, InstalledSkill
from superaide.platform.db.session import session_scope
from tests.integrations.gmail_fake import connect, gmail_fake_fixture, mail, owner_client_fixture  # noqa: F401
from tests.pipeline.test_loop import Script, tool_use, turn_for

MARLIN = "Marlin Bank <alerts@marlin.example>"
ALERT = (
    "Payment details: your Marlin Credit Card XX 4111 1111 1111 1111 was used for Rs 4,500.00 at TEAHOUSE on "
    "21-Oct-2026. Card number ending 1111, transaction ref 884201937. If this was not you, call us."
)
CODE = "Your one-time code is 834201. Do not share it. Password reset requested for your account."
PASSWORD = "correct horse battery"


def _setup(gmail_fake, *, extra=None):
    gmail_fake.add(mail("p1", thread="tp", sender=MARLIN, subject="Card transaction alert", body=ALERT))
    gmail_fake.add(mail("c1", thread="tc", sender="Bank <otp@marlin.example>", subject="Your login code", body=CODE))
    connect(scopes=(api.READONLY, api.COMPOSE), drafts=True)
    if extra:
        put(extra)


def put(values: dict) -> None:
    with session_scope() as s:
        row = s.query(InstalledSkill).filter(InstalledSkill.skill_id == gmail.SKILL_ID).one()
        row.settings = {**(row.settings or {}), "read": values}


def run_tool(name: str, args: dict):
    with session_scope() as s:
        from superaide.app.accounts import owner as hired_owner

        return gmail.run(s, hired_owner(s), name, args, tainted=False)


def text_of(out) -> str:
    content, _ = out
    return content if isinstance(content, str) else " ".join(b["content"][0]["text"] for b in content)


# --- defaults are today's behaviour ----------------------------------------------------------------


def test_a_missing_or_corrupt_setting_gives_todays_defaults():
    for raw in (None, {}, {"codes": "banana", "search_limit": 999, "blocked": "not-a-list"}):
        s = gmail_settings.from_raw(raw)
        assert (s.payment_preview, s.numbers, s.codes) == ("hide", "blanked", "never")
        assert (s.body_chars, s.search_limit, s.thread_depth, s.lookback_days) == (1500, 10, 5, 0)
        assert s.blocked == [] and s.allowed == []


def test_the_default_hides_a_payment_preview_blanks_numbers_and_names_the_setting(owner_client, gmail_fake):
    _setup(gmail_fake)
    found = text_of(run_tool("gmail_search", {"query": "marlin"}))
    assert "preview withheld" in found and "Payment and transaction emails: previews" in found
    assert "/dashboard/capabilities#gmail" in found and "Capabilities → Gmail" in found
    body = text_of(run_tool("gmail_read_thread", {"thread_id": "tp"}))
    assert "[number]" in body and "4111" not in body and "884201937" not in body


# --- the Marlin case ---------------------------------------------------------------------------------


def test_with_payment_previews_and_amounts_on_the_alert_is_read_and_the_amount_quoted(owner_client, gmail_fake):
    _setup(gmail_fake, extra={"payment_preview": "show", "numbers": "amounts"})
    found = text_of(run_tool("gmail_search", {"query": "marlin"}))
    assert "preview withheld" not in found.split("Card transaction alert")[1].split("Your login code")[0]
    body = text_of(run_tool("gmail_read_thread", {"thread_id": "tp"}))
    assert "Rs 4,500.00" in body and "TEAHOUSE" in body
    assert "4111" not in body and "884201937" not in body  # card and reference numbers stay blanked


# --- one-time codes: never by default, step-up to enable ---------------------------------------------


def test_codes_stay_hidden_by_default_and_the_hidden_text_names_the_setting(owner_client, gmail_fake):
    _setup(gmail_fake)
    body = text_of(run_tool("gmail_read_thread", {"thread_id": "tc"}))
    assert "834201" not in body and "One-time codes and password resets" in body
    assert "preview withheld" in text_of(run_tool("gmail_search", {"query": "code"}))


def test_enabling_codes_needs_the_password_and_is_audited(owner_client, gmail_fake):
    _setup(gmail_fake)
    url = "/app/gmail/read-settings"
    assert owner_client.put(url, json={"codes": "read"}).status_code == 403
    assert owner_client.put(url, json={"codes": "read", "confirm_password": "nope-nope-nope"}).status_code == 403
    assert "834201" not in text_of(run_tool("gmail_read_thread", {"thread_id": "tc"}))
    ok = owner_client.put(url, json={"codes": "read", "confirm_password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["settings"]["codes"] == "read"
    assert "834201" in text_of(run_tool("gmail_read_thread", {"thread_id": "tc"}))
    with session_scope() as s:
        actions = [a.action for a in s.query(AuditLog).all()]
    assert "gmail_read_setting_refused" in actions and "gmail_read_settings" in actions
    # turning it back off never needs the password
    assert owner_client.put(url, json={"codes": "never"}).status_code == 200
    assert "834201" not in text_of(run_tool("gmail_read_thread", {"thread_id": "tc"}))


def test_the_allowlist_never_opens_codes_and_an_email_cannot_change_settings(owner_client, gmail_fake):
    # red team: an allowed sender still cannot expose codes; no Gmail tool can write a setting
    _setup(gmail_fake, extra={"allowed": ["marlin.example"], "payment_preview": "hide"})
    assert "834201" not in text_of(run_tool("gmail_read_thread", {"thread_id": "tc"}))
    assert (
        "preview withheld"
        not in text_of(run_tool("gmail_search", {"query": "marlin"})).split("Card transaction")[1][:300]
    )
    names = {t for t in gmail.TOOLS}
    assert not any("setting" in n for n in names)
    with session_scope() as s:
        script = Script(tool_use("gmail_read_thread", {"thread_id": "tc"}), "ok")
        turn_for(s, script, "read my codes").run([{"role": "user", "content": "x"}])
        assert "834201" not in str(script.seen)


# --- sizes, lookback, lists --------------------------------------------------------------------------


def test_search_size_thread_depth_body_size_and_lookback_follow_the_settings(owner_client, gmail_fake):
    for i in range(8):
        gmail_fake.add(mail(f"m{i}", thread="tbig", subject=f"Msg {i}", body="x" * 4000 if i == 7 else "short"))
    connect(scopes=(api.READONLY,), drafts=False)
    put({"search_limit": 5, "thread_depth": 3, "body_chars": 3000, "lookback_days": 30})
    found = run_tool("gmail_search", {"query": "msg", "limit": 25})[0]
    assert len(found) == 5
    q = [dict(r.url.params).get("q", "") for r in gmail_fake.requests if r.url.path.endswith("/messages")]
    assert any("newer_than:30d" in x for x in q)
    thread = run_tool("gmail_read_thread", {"thread_id": "tbig"})[0]
    assert len(thread) == 3
    assert len(thread[-1]["content"][0]["text"]) > 2500  # 3000 characters allowed, not 1500


def test_a_blocked_sender_domain_and_label_is_never_read(owner_client, gmail_fake):
    _setup(gmail_fake)
    gmail_fake.add(mail("v1", thread="tv", sender="Doctor <dr@clinic.example>", subject="Results", body="private"))
    gmail_fake.add(
        mail("l1", thread="tl", sender="Ann <ann@x.example>", subject="Tax", body="secret", labels=("Label_9",))
    )
    put({"blocked": ["@clinic.example", "label:Label_9", "otp@marlin.example"]})
    found = text_of(run_tool("gmail_search", {"query": "all"}))
    assert "Results" not in found and "Tax" not in found and "Your login code" not in found
    assert "Card transaction alert" in found
    body = text_of(run_tool("gmail_read_thread", {"thread_id": "tv"}))
    assert "private" not in body and "Never read" in body


def test_the_prompt_describes_exactly_the_settings(owner_client, gmail_fake):
    _setup(
        gmail_fake, extra={"payment_preview": "show", "numbers": "amounts", "search_limit": 25, "blocked": ["a@b.co"]}
    )
    with session_scope() as s:
        text = "\n".join(b.text for b in turn_for(s, Script("x")).system())
    assert "previews are shown" in text and "amounts next to a currency are quoted" in text
    assert "25 emails per search" in text and "1 sender, domain or label entries" in text
    assert "Never say you 'can't verify'" in text or "can't verify" in text


# --- the route -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad", [{"payment_preview": "maybe"}, {"search_limit": 7}, {"blocked": ["not an address!"]}, {"codes": "yes"}]
)
def test_bad_values_are_refused(owner_client, gmail_fake, bad):
    _setup(gmail_fake)
    assert owner_client.put("/app/gmail/read-settings", json=bad).status_code == 422


def test_get_returns_every_setting_with_its_explanation(owner_client, gmail_fake):
    _setup(gmail_fake)
    out = owner_client.get("/app/gmail/read-settings").json()
    assert set(out["choices"]) == set(gmail_settings.CHOICES)
    assert out["choices"]["codes"]["default"] == "never" and out["settings"]["body_chars"] == 1500
    assert all(c["label"] and c["help"] for c in out["choices"].values())


BEC = "Accounts team <billing@vendor.example>"


def test_a_request_to_change_bank_details_has_its_own_switch_separate_from_transaction_alerts(owner_client, gmail_fake):
    # #433 follow-up: the invoice-fraud pattern is its own setting; a card alert is not affected by it
    gmail_fake.add(
        mail("b1", thread="tb", sender=BEC, subject="Our bank details have changed", body="Pay to the new account")
    )
    _setup(gmail_fake, extra={"payment_preview": "show"})  # transaction alerts shown...
    found = text_of(run_tool("gmail_search", {"query": "bank"}))
    assert "asks to change bank or account details" in found and "Bank and account change emails: previews" in found
    assert "Pay to the new account" not in found  # ...but the bank-change mail stays hidden
    assert "Payment details: your Marlin" in found
    put({"payment_preview": "show", "bank_change_preview": "show"})
    assert "Pay to the new account" in text_of(run_tool("gmail_search", {"query": "bank"}))


def test_the_first_run_hint_is_part_of_the_card_view(owner_client, gmail_fake):
    _setup(gmail_fake)
    out = owner_client.get("/app/gmail/read-settings").json()
    assert out["settings"]["bank_change_preview"] == "hide" and "bank_change_preview" in out["choices"]
    assert (
        owner_client.put("/app/gmail/read-settings", json={"bank_change_preview": "show"}).json()["settings"][
            "bank_change_preview"
        ]
        == "show"
    )


def test_each_setting_says_when_it_was_last_changed_from_the_audit_log(owner_client, gmail_fake):
    """#441: "last changed by/when" under a setting. Only settings that were changed have an entry."""
    _setup(gmail_fake)
    assert owner_client.get("/app/gmail/read-settings").json()["last_changed"] == {}
    assert owner_client.put("/app/gmail/read-settings", json={"numbers": "amounts"}).status_code == 200
    out = owner_client.get("/app/gmail/read-settings").json()
    assert set(out["last_changed"]) == {"numbers"}
    assert out["last_changed"]["numbers"]["by"] == "you" and out["last_changed"]["numbers"]["at"].startswith("20")
    # A later change to another setting leaves the first one's entry alone.
    owner_client.put("/app/gmail/read-settings", json={"search_limit": 5})
    later = owner_client.get("/app/gmail/read-settings").json()["last_changed"]
    assert set(later) == {"numbers", "search_limit"} and later["numbers"] == out["last_changed"]["numbers"]


def test_the_gmail_status_says_when_drafts_were_last_switched(owner_client, gmail_fake):
    _setup(gmail_fake)
    assert owner_client.get("/app/gmail").json()["last_changed"] == {}
    owner_client.post("/app/gmail/drafts", json={"enabled": False})
    assert set(owner_client.get("/app/gmail").json()["last_changed"]) == {"drafts"}
