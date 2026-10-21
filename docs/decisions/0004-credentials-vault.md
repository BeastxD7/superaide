# 4. Credentials connected in the app, encrypted at rest

Date: 2026-10-21. Status: accepted.

## Context

Owners may not be technical, so they shouldn't have to edit `.env`. Anthropic offers no OAuth for third-party apps, and Telegram and Slack give tokens. WhatsApp's business terms restrict general-purpose AI assistants.

## Decision

- The owner pastes keys into the app.
- Each one is checked with its service, then encrypted with AES-256-GCM using the provider and field as associated data.
- The master key comes from `SECRETS_MASTER_KEY`, else the OS keychain, else a 0600 key file.
- Only a hint (`sk-…4f2a`) is ever shown back. Stored values override `.env`.
- WhatsApp is shown as unavailable rather than connected through unofficial libraries.

## Consequences

- No secrets in logs, responses or the frontend.
- For hosted deployments, move the master key to a KMS (envelope encryption) and rotate it.
- Losing the master key makes stored credentials unreadable: owners reconnect.
