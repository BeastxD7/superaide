"""Store a made-up Claude key as the owner's own key for one instance (as if pasted on the AI model page).
argv: tenant_id agent_id key. Runs with the slot's DATABASE_URL and SECRETS_MASTER_KEY."""
import sys, uuid
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope, use_instance
from superaide.vault import store

tenant, agent, key = sys.argv[1:4]
with use_instance(InstanceScope(uuid.UUID(tenant), uuid.UUID(agent))), session_scope() as s:
    store.save(s, "anthropic", {"api_key": key}, user_id=None, meta={})
print("saved")
