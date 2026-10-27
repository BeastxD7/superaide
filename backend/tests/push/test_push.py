"""ADR 0011 M1-M3: push tables, VAPID keys, the subscribe API, privacy-safe payloads and the sender.

A fake push service (respx) stands in for Apple's, Google's and Mozilla's; each test device has a
real P-256 key pair, so what the sender encrypted can be decrypted here and checked.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass

import http_ece
import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from superaide.features.push import payload, send, subscriptions, vapid
from superaide.features.push.models import PushSend, PushSubscription
from superaide.identity.auth.models import IntegrationSecret
from superaide.identity.models import Person
from superaide.identity.vault import crypto
from superaide.identity.vault.crypto import VaultError
from superaide.main import create_app
from superaide.platform.config import get_settings
from superaide.platform.db import rls
from superaide.platform.db.models import AuditLog, Base
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope, use_instance
from superaide.platform.tenancy.models import PlatformState

PASSWORD = "correct horse battery"
APPLE = "https://web.push.apple.com/QGuQyavXutnMH6m2"
GOOGLE = "https://fcm.googleapis.com/fcm/send/dXk"
# Free text that must never reach a lock screen, whatever the level.
SECRETS_IN_ITEM = ("$4,200", "Hotel Majestic", "divorce papers", "Project Falcon offsite", "call me at 5")


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"p" * 32).decode())
    monkeypatch.delenv("SECRETS_MASTER_KEY_FILE", raising=False)
    crypto.master_key.cache_clear()
    yield
    crypto.master_key.cache_clear()


@pytest.fixture
def push_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "push_enabled", True)


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass
class Device:
    """A browser's side of a subscription: its private key decrypts what was pushed to it."""

    endpoint: str
    key: ec.EllipticCurvePrivateKey
    auth: bytes

    @classmethod
    def make(cls, endpoint: str) -> Device:
        return cls(endpoint, ec.generate_private_key(ec.SECP256R1()), os.urandom(16))

    def body(self, platform: str = "ios", label: str = "") -> dict[str, object]:
        point = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": b64u(point), "auth": b64u(self.auth)},
            "platform": platform,
            "label": label,
        }

    def secret(self) -> subscriptions.Secret:
        keys = self.body()["keys"]
        assert isinstance(keys, dict)
        return subscriptions.Secret(self.endpoint, keys["p256dh"], keys["auth"])

    def decrypt(self, body: bytes) -> bytes:
        return http_ece.decrypt(body, private_key=self.key, auth_secret=self.auth, version="aes128gcm")


@pytest.fixture
def browser(agent):
    app = create_app(agent)

    def make() -> TestClient:
        return TestClient(app, headers={"X-Requested-With": "superaide"})

    return make


def hired(client: TestClient, email: str = "jordan@example.com") -> TestClient:
    r = client.post("/auth/signup", json={"name": "Jordan Whitfield", "email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert client.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return client


# --- M1: tables and keys ----------------------------------------------------------


def test_push_tables_are_per_instance_and_under_rls():
    for name in ("push_subscription", "push_send"):
        table = Base.metadata.tables[name]
        assert {"agent_id", "tenant_id"} <= set(table.columns.keys()), name
        assert name in rls.TABLES, name


def test_no_vapid_keys_while_push_is_off():
    with session_scope() as s:
        assert vapid.ensure(s) is None
    with session_scope() as s:
        assert s.get(PlatformState, vapid.KEY) is None


def test_vapid_keys_are_made_once_and_stored_encrypted(push_on):
    with session_scope() as s:
        first = vapid.ensure(s)
    with session_scope() as s:
        again = vapid.ensure(s)
        row = s.get(PlatformState, vapid.KEY)
        assert row is not None
        stored = json.dumps(row.value)
    assert first is not None and again == first
    assert len(base64.urlsafe_b64decode(first.public_key + "=")) == 65  # uncompressed P-256 point
    assert "PRIVATE KEY" not in stored and first.private_pem not in stored
    with session_scope() as s:
        audits = s.scalars(select(AuditLog).where(AuditLog.action == "vapid_generated")).all()
        assert len(audits) == 1 and "PRIVATE" not in json.dumps(audits[0].detail)
        # Never in an instance's vault: T3 binds those rows to one instance.
        assert s.scalar(select(func.count()).select_from(IntegrationSecret)) == 0


def test_vapid_keys_are_per_deployment_so_every_instance_reads_the_same_pair(push_on):
    a = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
    b = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
    with use_instance(a), session_scope() as s:
        made = vapid.ensure(s)
    with use_instance(b), session_scope() as s:
        seen, ensured = vapid.load(s), vapid.ensure(s)
    assert made is not None and seen == made and ensured == made
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(PlatformState).where(PlatformState.key == vapid.KEY)) == 1


def test_the_vapid_private_key_decrypts_only_under_its_platform_slot(push_on):
    with session_scope() as s:
        vapid.ensure(s)
        row = s.get(PlatformState, vapid.KEY)
        assert row is not None
        with pytest.raises(VaultError):
            crypto.decrypt(row.value["private_enc"], f"{uuid.uuid4()}/platform/vapid")


def test_a_sealed_subscription_moved_to_another_instance_or_row_fails_to_decrypt():
    device = Device.make(APPLE)
    agent, row_id = uuid.uuid4(), uuid.uuid4()
    sealed = subscriptions.seal(device.secret(), agent, row_id)
    assert APPLE not in sealed
    good = PushSubscription(id=row_id, agent_id=agent, secret_enc=sealed)
    assert subscriptions.open_secret(good).endpoint == APPLE
    for other in (PushSubscription(id=row_id, agent_id=uuid.uuid4(), secret_enc=sealed),
                  PushSubscription(id=uuid.uuid4(), agent_id=agent, secret_enc=sealed)):  # fmt: skip
        with pytest.raises(VaultError):
            subscriptions.open_secret(other)


def test_only_known_https_push_services_are_accepted():
    for ok in (
        APPLE,
        GOOGLE,
        "https://updates.push.services.mozilla.com/wpush/v2/x",
        "https://wns2-db5p.notify.windows.com/w/?token=x",
    ):
        assert subscriptions.push_host(ok)
    for bad in ("http://web.push.apple.com/x", "https://169.254.169.254/latest", "https://localhost/x",
                "https://evilpush.apple.com.attacker.io/x", "https://web.push.apple.com:8443/x",
                "https://user:pw@web.push.apple.com/x", "ftp://fcm.googleapis.com/x"):  # fmt: skip
        with pytest.raises(subscriptions.BadSubscription):
            subscriptions.push_host(bad)


# --- M2: the subscribe API -----------------------------------------------------------


def test_every_push_route_is_404_while_push_is_off(browser):
    client = hired(browser())
    device = Device.make(APPLE)
    assert client.get("/app/push/key").status_code == 404
    assert client.put("/app/push/subscription", json=device.body()).status_code == 404
    assert client.get("/app/push/devices").status_code == 404
    assert client.post("/app/push/test").status_code == 404
    assert browser().get("/app/push/key").status_code == 401  # signed out: like every owner route


def test_subscribe_list_and_unsubscribe(browser, push_on):
    client = hired(browser())
    key = client.get("/app/push/key")
    assert key.status_code == 200 and len(key.json()["public_key"]) == 87
    device = Device.make(APPLE)
    r = client.put("/app/push/subscription", json=device.body(label="Jordan's iPhone"))
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True
    view = r.json()["device"]
    assert view["this_device"] is True and view["preview_level"] == "none" and view["host"] == "web.push.apple.com"
    assert APPLE not in r.text  # the endpoint is a secret: never echoed back

    # Every app start re-saves it: same row, refreshed.
    again = client.put("/app/push/subscription", json=device.body())
    assert again.json()["created"] is False and again.json()["device"]["id"] == view["id"]
    assert again.json()["device"]["label"] == "Jordan's iPhone"
    devices = client.get("/app/push/devices").json()
    assert [d["id"] for d in devices] == [view["id"]] and APPLE not in json.dumps(devices)

    with session_scope() as s:
        row = s.scalars(select(PushSubscription)).one()
        assert row.session_id is not None  # bound to this browser session
        assert row.endpoint_hash == subscriptions.endpoint_hash(APPLE) and APPLE not in row.secret_enc

    r = client.request("DELETE", "/app/push/subscription", json={"endpoint": APPLE})
    assert r.status_code == 200
    assert client.get("/app/push/devices").json() == []
    assert client.request("DELETE", "/app/push/subscription", json={"endpoint": APPLE}).status_code == 404
    with session_scope() as s:
        actions = set(s.scalars(select(AuditLog.action)))
        assert {"push_subscribed", "push_unsubscribed"} <= actions
        assert APPLE not in json.dumps([a.detail for a in s.scalars(select(AuditLog))])


def test_subscribing_needs_the_csrf_header_a_session_and_a_hire(browser, push_on):
    client = hired(browser())
    device = Device.make(APPLE)
    bare = TestClient(client.app, cookies=client.cookies)
    assert bare.put("/app/push/subscription", json=device.body()).status_code == 403
    assert browser().put("/app/push/subscription", json=device.body()).status_code == 401
    other = browser()
    other.post("/auth/signup", json={"name": "Priya Tan", "email": "priya@example.com", "password": PASSWORD})
    assert other.put("/app/push/subscription", json=device.body()).status_code == 403  # didn't hire Ava


def test_bad_subscriptions_are_refused(browser, push_on):
    client = hired(browser())
    device = Device.make(APPLE)
    evil = {**device.body(), "endpoint": "https://169.254.169.254/latest/meta-data"}
    assert client.put("/app/push/subscription", json=evil).status_code == 422
    short = device.body()
    short["keys"] = {"p256dh": "A" * 87, "auth": b64u(os.urandom(16))}  # not a point on the curve's format
    assert client.put("/app/push/subscription", json=short).status_code == 422
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(PushSubscription)) == 0


def test_a_device_can_be_renamed_previewed_muted_and_removed(browser, push_on):
    client = hired(browser())
    device_id = client.put("/app/push/subscription", json=Device.make(GOOGLE).body("android")).json()["device"]["id"]
    r = client.patch(f"/app/push/devices/{device_id}", json={"label": "Pixel", "preview_level": "sender",
                                                             "mute_minutes": 60})  # fmt: skip
    assert r.status_code == 200 and r.json()["label"] == "Pixel" and r.json()["preview_level"] == "sender"
    assert r.json()["muted_until"] is not None
    assert client.patch(f"/app/push/devices/{device_id}", json={"preview_level": "everything"}).status_code == 422
    assert client.patch(f"/app/push/devices/{device_id}", json={"mute_minutes": 0}).json()["muted_until"] is None
    assert client.request("DELETE", f"/app/push/devices/{device_id}").status_code == 200
    assert client.get("/app/push/devices").json() == []
    assert client.request("DELETE", f"/app/push/devices/{uuid.uuid4()}").status_code == 404


def test_another_account_saving_the_same_browser_takes_it_over_with_fresh_settings(browser, push_on):
    """Same browser profile, a different account signs in: the row moves and keeps no old choices."""
    owner = hired(browser())
    device = Device.make(APPLE)
    device_id = owner.put("/app/push/subscription", json=device.body()).json()["device"]["id"]
    owner.patch(f"/app/push/devices/{device_id}", json={"preview_level": "reason"})
    with session_scope() as s:
        row = s.get(PushSubscription, uuid.UUID(device_id))
        assert row is not None
        from superaide.identity.auth.models import UserAccount

        newcomer = Person(display_name="Someone", relation="manager")
        s.add(newcomer)
        s.flush()
        account = UserAccount(email="n@example.com", email_normalized="n@example.com", password_hash="x",
                              display_name="Someone", person_id=newcomer.id)  # fmt: skip
        s.add(account)
        s.flush()
        moved, created = subscriptions.upsert(
            s, secret=device.secret(), user_id=account.id, person_id=newcomer.id, session_id=None,
            agent_id=row.agent_id, platform="ios", label="", default_preview="none",
        )  # fmt: skip
        assert moved.user_id == account.id and moved.preview_level == "none"
        assert created is False and moved.id == row.id


# --- M3: payloads ----------------------------------------------------------------------

LEVELS = ("none", "sender", "reason")
TOPICS = (frozenset(), frozenset({"legal_hr"}), frozenset({"payment"}), frozenset({"health"}),
          frozenset({"never_share"}))  # fmt: skip


def _item(**kw) -> payload.PushItem:
    base = {"kind": "escalation", "token": "AbCdEfGh1234", "sender_first_name": "Priya Tan", "sender_known": True,
            "reason": "deadline_today"}  # fmt: skip
    return payload.PushItem(**{**base, **kw})


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("topics", TOPICS)
def test_payload_snapshots_per_level_and_topic(level, topics):
    item = _item(topics=topics)
    title, body, used = payload.preview(level, item)
    assert title == "Ava"
    if topics:  # legal/HR, payment, health and never-share fall back to the content-free preview
        assert (body, used) == ("Needs you", "none")
    else:
        expected = {"none": "Needs you", "sender": "Priya needs you", "reason": "Priya: deadline today"}
        assert body == expected[level] and used == level


@pytest.mark.parametrize("level", LEVELS)
def test_strangers_and_unverified_senders_never_show_a_name(level):
    for item in (_item(sender_known=False), _item(sender_known=False, reason="")):
        _, body, _ = payload.preview(level, item)
        assert "Priya" not in body


def test_odd_names_and_unknown_reasons_fall_back():
    assert payload.preview("sender", _item(sender_first_name="+1 555 0100"))[1] == "Needs you"
    assert payload.preview("sender", _item(sender_first_name="<script>"))[1] == "Needs you"
    assert payload.preview("reason", _item(reason="the model's own words"))[1] == "Priya needs you"
    assert payload.preview("everything", _item())[1] == "Needs you"
    assert payload.preview("none", _item(count=3))[1] == "3 things need you"


@pytest.mark.parametrize("kind", ["digest", "reminder", "approval", "test"])
@pytest.mark.parametrize("level", LEVELS)
def test_other_pushes_are_content_free_at_every_level(kind, level):
    title, body, used = payload.preview(level, _item(kind=kind))
    assert (title, body, used) == ("Ava", payload.BODIES[kind], "none") and "Priya" not in body


def test_the_body_never_carries_message_content_and_is_a_fixed_size():
    sizes = set()
    for level in LEVELS:
        for topics in TOPICS:
            for kind in ("escalation", "digest", "reminder", "approval", "test"):
                for count in (1, 12):
                    built = payload.build(_item(kind=kind, topics=topics, count=count), level,
                                          base_url="https://app.example", instance=str(uuid.uuid4()))  # fmt: skip
                    text = json.dumps(built)
                    assert not any(s in text for s in SECRETS_IN_ITEM)
                    assert built["web_push"] == 8030 and built["notification"]["title"] == "Ava"
                    assert built["notification"]["navigate"].startswith("https://app.example/dashboard")
                    raw = payload.encode(built, 1024)
                    assert json.loads(raw) == built
                    sizes.add(len(raw))
    assert sizes == {1024}


def test_escalation_body_uses_the_opaque_token_only():
    item = _item()
    built = payload.build(item, "none", base_url="https://app.example/", instance="inst")
    assert built["notification"]["navigate"] == "https://app.example/dashboard/needs-you?d=AbCdEfGh1234"
    assert built["notification"]["tag"] == "esc-AbCdEfGh1234" and built["notification"]["app_badge"] == 1
    assert built["superaide"] == {"v": 1, "instance": "inst", "token": "AbCdEfGh1234", "kind": "escalation",
                                  "actions": ["ack", "snooze30"]}  # fmt: skip
    assert payload.headers(item, 180) == {"TTL": "180", "Urgency": "high", "Topic": "esc-AbCdEfGh1234"}
    assert payload.headers(_item(kind="digest"), 180)["Urgency"] == "low"
    with pytest.raises(ValueError):
        _item(token="not/a token")


def test_a_payload_bigger_than_the_padding_is_a_bug():
    with pytest.raises(payload.PayloadTooLarge):
        payload.encode({"x": "y" * 2000}, 1024)


# --- M3: the sender ----------------------------------------------------------------------


def _subscribe(*devices: Device, level: str = "none") -> list[PushSubscription]:
    with session_scope() as s:
        owner = Person(display_name="Jordan", relation="manager")
        s.add(owner)
        s.flush()
        from superaide.identity.auth.models import UserAccount

        user = UserAccount(email="o@example.com", email_normalized="o@example.com", password_hash="x",
                           display_name="Jordan", person_id=owner.id)  # fmt: skip
        s.add(user)
        s.flush()
        rows = []
        for d in devices:
            row, _ = subscriptions.upsert(
                s, secret=d.secret(), user_id=user.id, person_id=owner.id, session_id=None,
                agent_id=owner.agent_id, platform="ios", label="", default_preview=level,
            )  # fmt: skip
            rows.append(row)
        for row in rows:
            s.expunge(row)
        return rows


def _send(item: payload.PushItem, **kw) -> list[send.Outcome]:
    with session_scope() as s:
        rows = s.scalars(select(PushSubscription).order_by(PushSubscription.created_at)).all()
        return send.send(s, item, rows, purpose=kw.pop("purpose", "test"), instance="inst",
                         sleep=lambda _: None, **kw)  # fmt: skip


@respx.mock
def test_send_encrypts_pads_and_sets_the_headers(push_on):
    apple, google = Device.make(APPLE), Device.make(GOOGLE)
    _subscribe(apple, google, level="sender")
    with session_scope() as s:
        keys = vapid.ensure(s)
    assert keys is not None
    seen: dict[str, httpx.Request] = {}

    def service(request: httpx.Request) -> httpx.Response:
        seen[str(request.url)] = request
        return httpx.Response(201)

    respx.post(APPLE).mock(side_effect=service)
    respx.post(GOOGLE).mock(side_effect=service)
    out = _send(_item(), purpose="escalation")
    assert [o.status for o in out] == ["sent", "sent"]
    bodies = []
    for device in (apple, google):
        req = seen[device.endpoint]
        assert req.headers["Content-Encoding"] == "aes128gcm"
        assert req.headers["TTL"] == "180" and req.headers["Urgency"] == "high"
        assert req.headers["Topic"] == "esc-AbCdEfGh1234"
        assert (
            req.headers["Authorization"].startswith("vapid t=")
            and f"k={keys.public_key}" in req.headers["Authorization"]
        )
        plain = device.decrypt(req.content)
        assert len(plain) == get_settings().push_pad_bytes
        body = json.loads(plain)
        assert body["notification"]["body"] == "Priya needs you"
        bodies.append(len(req.content))
    assert len(set(bodies)) == 1  # every device's ciphertext is the same size
    with session_scope() as s:
        sends = s.scalars(select(PushSend)).all()
        assert {r.status for r in sends} == {"sent"} and all(r.http_status == 201 for r in sends)
        assert s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "push_sent")) == 1


@respx.mock
@pytest.mark.parametrize("code", [404, 410])
def test_a_dead_subscription_becomes_gone(push_on, code):
    _subscribe(Device.make(APPLE))
    with session_scope() as s:
        vapid.ensure(s)
    respx.post(APPLE).mock(return_value=httpx.Response(code))
    assert [o.status for o in _send(_item())] == ["gone"]
    with session_scope() as s:
        row = s.scalars(select(PushSubscription)).one()
        assert row.gone_at is not None and row.enabled is False
        assert s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "push_gone")) == 1
    assert [o.status for o in _send(_item())] == ["skipped"]  # never tried again


@respx.mock
def test_413_is_not_retried_and_429_and_5xx_are(push_on):
    _subscribe(Device.make(APPLE))
    with session_scope() as s:
        vapid.ensure(s)
    route = respx.post(APPLE).mock(return_value=httpx.Response(413))
    out = _send(_item())
    assert out[0].status == "failed" and out[0].attempts == 1 and route.call_count == 1

    route = respx.post(APPLE).mock(side_effect=[httpx.Response(429), httpx.Response(503), httpx.Response(201)])
    out = _send(_item(token="ZZZZzzzz1234"))
    assert out[0].status == "sent" and out[0].attempts == 3

    respx.post(APPLE).mock(side_effect=httpx.ConnectError("down"))
    out = _send(_item(token="YYYYyyyy1234"))
    assert out[0].status == "failed" and out[0].attempts == get_settings().push_max_attempts


@respx.mock
def test_muted_devices_are_skipped(push_on):
    from datetime import UTC, datetime, timedelta

    _subscribe(Device.make(APPLE))
    with session_scope() as s:
        vapid.ensure(s)
        s.scalars(select(PushSubscription)).one().muted_until = datetime.now(UTC) + timedelta(hours=1)
    route = respx.post(APPLE).mock(return_value=httpx.Response(201))
    assert [o.status for o in _send(_item())] == ["skipped"] and route.call_count == 0


def test_sending_without_keys_refuses():
    _subscribe(Device.make(APPLE))
    with pytest.raises(RuntimeError):
        _send(_item())


@respx.mock
def test_the_test_endpoint_reaches_the_owners_devices(browser, push_on):
    client = hired(browser())
    apple = Device.make(APPLE)
    assert client.post("/app/push/test").status_code == 409  # no device yet
    client.put("/app/push/subscription", json=apple.body())
    captured: list[bytes] = []
    respx.post(APPLE).mock(side_effect=lambda r: captured.append(r.content) or httpx.Response(201))
    r = client.post("/app/push/test")
    assert r.status_code == 200 and r.json() == {"sent": 1, "devices": 1}
    body = json.loads(apple.decrypt(captured[0]))
    assert body["notification"]["body"] == "Ava can reach this phone"
    for _ in range(4):
        client.post("/app/push/test")
    assert client.post("/app/push/test").status_code == 429
