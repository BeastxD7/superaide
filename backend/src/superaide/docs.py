"""What /docs shows: the overview, the sections (tags) in order, and how the page behaves."""

from __future__ import annotations

DESCRIPTION = """
The backend for **Superaide**: Ava's marketplace page, accounts, hiring, the introduction,
the owner's chat and notebook, connections to Claude/Telegram/Slack, the operator console and
the channel webhooks. JSON only; the web app on port 3000 calls it through its `/api` proxy.

### How requests are authorised

| Who | How | Used by |
| --- | --- | --- |
| A signed-in person | the `superaide_session` cookie (httpOnly) set by **Log in** / **Sign up** | Accounts, Owner sections |
| Any change (POST) from a browser | the `X-Requested-With` header, any value (CSRF protection) | every POST under `/auth` and `/app` |
| The operator | `Authorization: Bearer <ADMIN_TOKEN>` from `.env` | Operator console, Simulator |
| Telegram / Slack | the webhook secret / Slack request signature | Webhooks |

**Trying it here:** press **Authorize**, put any value (say `docs`) in *X-Requested-With*, and
the admin token in *Admin token* if you need the console. Then call **Log in**: the cookie it
sets is sent with the next calls from this page.

Owner endpoints answer `403` until the signed-in account has hired Ava. Without a Claude key
the brain runs in offline mode: replies are placeholders and nothing is spent.
"""

TAGS = [
    {"name": "Health", "description": "Is the API up?"},
    {"name": "Accounts", "description": "Sign up, log in and out, change the password, see who is signed in."},
    {"name": "Marketplace & hiring", "description": "The agents on offer, and hiring Ava (one owner per agent)."},
    {
        "name": "Introduction",
        "description": "Ava getting to know the owner: the eight steps, files and links to read, and the video call.",
    },
    {
        "name": "Owner: chat & notebook",
        "description": "Talking to Ava, and everything Ava knows about the owner, with where it came from.",
    },
    {"name": "Owner: approvals", "description": "Actions Ava was asked to take that wait for the owner's yes or no."},
    {"name": "Owner: people & rules", "description": "Who matters to the owner and the standing rules Ava follows."},
    {
        "name": "Connections",
        "description": "Connect Claude, Telegram and Slack. Each credential is checked with the service, encrypted, "
        "and never shown again (only a hint like `sk-…4f2a`).",
    },
    {
        "name": "Operator console",
        "description": "Read-only views for the operator, the kill switch and manual identity links. "
        "Needs the admin token.",
    },
    {"name": "Simulator", "description": "Inject a message as any account to try impostor scenarios. Dev only."},
    {
        "name": "Webhooks",
        "description": "Called by Telegram and Slack, not by people. Unverified requests are dropped and audited.",
    },
]

SWAGGER_UI = {
    "docExpansion": "none",  # sections start folded, so the page reads as a table of contents
    "filter": True,  # a search box over the endpoints
    "tryItOutEnabled": True,
    "persistAuthorization": True,
    "displayRequestDuration": True,
    "defaultModelsExpandDepth": 0,
}
