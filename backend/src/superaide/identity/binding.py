"""Binding existing links to the bot they were made on (used by migration 0018 and its test).

Links made before links were bound have no endpoint. The migration binds each tenant's linked
Telegram accounts to the bot connected now, read from the environment or the encrypted vault. Where
the bot can't be read (no token, or the vault key isn't available to the migration), the link stays
unbound and is bound by the resolver to the bot of its next verified message (audited as
`endpoint_bound`), which is the bot the owner connected in the app.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

log = logging.getLogger(__name__)


def bind_unbound_telegram(conn: Connection, bot: str, tenant_id: uuid.UUID | None = None) -> int:
    """Bind linked, unbound Telegram accounts (of one tenant, or all) to `bot`. Returns rows changed."""
    if not bot:
        return 0
    sql = (
        "UPDATE channel_identity SET endpoint_key = :bot "
        "WHERE provider = 'telegram' AND person_id IS NOT NULL AND endpoint_key = ''"
    )
    params: dict[str, object] = {"bot": bot}
    if tenant_id is not None:
        sql += " AND tenant_id = :tenant"
        params["tenant"] = tenant_id
    return conn.execute(text(sql), params).rowcount or 0


def current_bots(conn: Connection) -> list[tuple[uuid.UUID | None, str]]:
    """(tenant, bot id) for each Telegram bot token saved in the app, else the one from the environment."""
    from superaide.channels.base import bot_id
    from superaide.config import get_settings

    found: list[tuple[uuid.UUID | None, str]] = []
    rows = conn.execute(
        text("SELECT tenant_id, ciphertext FROM integration_secret WHERE provider = 'telegram' AND name = 'bot_token'")
    ).all()
    for tenant, ciphertext in rows:
        try:
            from superaide.vault.crypto import decrypt
            from superaide.vault.store import slot

            found.append((tenant, bot_id(decrypt(ciphertext, slot("telegram", "bot_token")))))
        except Exception:  # no master key here: the resolver binds on the next message instead
            log.warning("couldn't read the saved Telegram bot token; links bind on their next message")
    env_bot = bot_id(get_settings().telegram_bot_token)
    if env_bot and not rows:  # a token saved in the app takes precedence over .env (vault/store.py)
        found.append((None, env_bot))
    return found


def backfill(conn: Connection) -> int:
    return sum(bind_unbound_telegram(conn, bot, tenant) for tenant, bot in current_bots(conn) if bot)
