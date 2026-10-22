"""Changing an account's email: both columns move together, the new address is verified again,
old links stop working, and the database refuses a hand edit that splits them."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from superaide.db.models import UserAccount
from superaide.db.session import session_scope
from tests.api.conftest import OTHER, OWNER, PASSWORD, login, signup
from tests.api.test_email_links import outbox, token_in  # noqa: F401  (outbox is an autouse fixture)


def user(email: str) -> UserAccount | None:
    with session_scope() as s:
        found = s.scalar(select(UserAccount).where(UserAccount.email_normalized == email))
        if found is not None:
            s.expunge(found)
        return found


def test_changing_email_moves_both_columns_and_asks_to_verify_again(browser, outbox):  # noqa: F811
    c = browser()
    signup(c)
    old_link = token_in(outbox[0], "/verify-email")
    r = c.post("/auth/email", json={"email": "  Shashank.New@Example.COM ", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is False
    moved = user("shashank.new@example.com")
    assert moved is not None and moved.email == "Shashank.New@Example.COM" and user(OWNER["email"]) is None
    assert outbox[-1].to.lower() == "shashank.new@example.com"  # the new address gets the verification link
    # The link sent to the old address no longer verifies anything; the new one does.
    assert browser().post("/auth/verify-email", json={"token": old_link}).status_code == 400
    assert (
        browser().post("/auth/verify-email", json={"token": token_in(outbox[-1], "/verify-email")}).status_code == 200
    )
    assert login(browser(), "shashank.new@example.com").status_code == 200
    assert login(browser(), OWNER["email"]).status_code == 401


def test_changing_email_needs_the_password_and_a_free_valid_address(browser):
    c = browser()
    signup(c)
    signup(browser(), **OTHER)
    assert c.post("/auth/email", json={"email": "x@example.com", "password": "wrong password"}).status_code == 401
    assert c.post("/auth/email", json={"email": OTHER["email"], "password": PASSWORD}).status_code == 409
    assert c.post("/auth/email", json={"email": "not an email", "password": PASSWORD}).status_code == 422
    assert c.post("/auth/email", json={"email": OWNER["email"].upper(), "password": PASSWORD}).status_code == 422
    assert browser(csrf=False).post(
        "/auth/email", json={"email": "y@example.com", "password": PASSWORD}
    ).status_code in (401, 403)
    assert user(OWNER["email"]) is not None


def test_setting_the_address_in_code_keeps_the_normalized_one_in_step():
    account = UserAccount(email="Mixed.Case@Example.com", password_hash="x", display_name="M")
    assert account.email_normalized == "mixed.case@example.com"


def test_the_database_refuses_a_hand_edit_that_splits_the_two_columns(browser):
    signup(browser())
    with pytest.raises(IntegrityError), session_scope() as s:
        s.execute(text("UPDATE user_account SET email = 'shashank@example.cmo'"))


def test_a_refused_change_at_the_hourly_email_limit_leaves_the_address_alone(browser, outbox):  # noqa: F811
    from superaide.config import get_settings

    c = browser()
    signup(c)
    for _ in range(get_settings().email_links_per_account_per_hour - 1):  # sign-up sent one; resends reach the limit
        assert c.post("/auth/verify-email/send").status_code == 200
    sent = len(outbox)
    r = c.post("/auth/email", json={"email": "moved@example.com", "password": PASSWORD})
    assert r.status_code == 429 and "several emails" in r.json()["detail"]
    assert user("moved@example.com") is None and user(OWNER["email"]) is not None  # nothing was changed
    assert len(outbox) == sent
    assert login(browser(), OWNER["email"]).status_code == 200  # the old address still logs in
    # The refusal does not reveal whether the password was right.
    assert c.post("/auth/email", json={"email": "moved@example.com", "password": "wrong password"}).status_code == 429


def test_the_link_for_the_new_address_points_at_the_address_in_use(browser, outbox):  # noqa: F811
    c = browser()
    signup(c)
    r = c.post(
        "/auth/email",
        json={"email": "moved@example.com", "password": PASSWORD},
        headers={"Origin": "http://127.0.0.1:3000"},
    )
    assert r.status_code == 200
    assert "http://127.0.0.1:3000/verify-email#token=" in outbox[-1].text
    r = c.post(
        "/auth/email",
        json={"email": "again@example.com", "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 200 and "https://evil.example" not in outbox[-1].text  # never someone else's site
