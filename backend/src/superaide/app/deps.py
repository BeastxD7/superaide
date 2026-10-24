"""FastAPI dependencies and small helpers shared by the person-facing routers.

Every module that serves `/api/app/...` endpoints needs `owner_person_id` (the signed-in account's Person in the
agent it hired, with that agent's instance in scope). It used to live in `app/routes.py`, so ~30 routers imported
the 1,500-line router module and every change to anything `app.routes` depends on selected all their tests
(ADR 0015, technique 5). `app.routes` re-exports these names.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from superaide.app import accounts
from superaide.auth.routes import current_user_id
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope, use_instance


def slack_connected(settings: Any) -> bool:
    return bool(settings.slack_bot_token and settings.slack_app_token)


@lru_cache
def telegram_username(token: str) -> str | None:
    """The bot's @username, needed for t.me links. None if Telegram isn't set up."""
    if not token:
        return None
    try:
        import httpx

        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        return r.json().get("result", {}).get("username")
    except Exception:
        return None


def _hired_instance(user_id: uuid.UUID) -> tuple[InstanceScope, uuid.UUID] | None:
    with session_scope() as s:
        return accounts.instance_of(s, user_id)


async def owner_person_id(user_id: uuid.UUID = Depends(current_user_id)) -> AsyncIterator[uuid.UUID]:
    """The signed-in account's Person in the agent instance it hired, or 403.

    Also puts that instance in scope for the rest of the request (ADR 0009): everything the
    endpoint writes belongs to it, and Row-Level Security shows only its rows. An async
    dependency, so the scope it sets is the one the endpoint's thread runs with.
    """
    found = await run_in_threadpool(_hired_instance, user_id)
    if found is None:
        raise HTTPException(403, "Hire Ava first.")
    scope, person_id = found
    with use_instance(scope):
        yield person_id
