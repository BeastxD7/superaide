# Security

## Reporting a problem

Please don't open a public issue. Use GitHub's **Report a vulnerability** (Security tab) on this repository, or contact the maintainer, @BeastxD7, directly. Include what you found, how to reproduce it, and what an attacker could do. You'll get an answer within a few working days.

## Security model

- **Identity.** People are recognised by immutable platform IDs, never by display names or what a message claims. A new channel joins a person only through a one-time code sent to a channel already trusted (6 digits, 10-minute expiry, locked after 5 wrong tries). Every link is audited and reversible.
- **What the assistant may reveal.** Memories carry a visibility. The audience gate filters them in SQL to what everyone in the conversation may see, before the prompt is built. The owner brief given to non-owners contains only what the owner chose to share.
- **Actions.** The model can only propose actions. Code decides from stored roles, and sensitive actions need the owner's approval.
- **Accounts.**
  - Argon2id passwords.
  - Server-side sessions in httpOnly SameSite=Lax cookies; only token hashes are stored.
  - Lockout after repeated failures and per-IP throttling.
  - CSRF header check and a CORS allowlist.
  - An audit trail of sign-in events.
- **Credentials.** Encrypted with AES-256-GCM. The master key is in the OS keychain or `SECRETS_MASTER_KEY`. Credentials are never logged or returned, only a short hint.
- **Spend.** Every model call reserves its worst-case cost against a hard cap, and a kill switch stops all calls.
- **Fetching links.** Only http(s) on ports 80/443. Private, loopback and link-local addresses are refused, including after DNS resolution. Responses are size-capped.

## Deployment checklist

- [ ] Serve over HTTPS and set `SESSION_COOKIE_SECURE=true`.
- [ ] Set `FRONTEND_ORIGIN` to the real app origin.
- [ ] Keep the admin API private (it binds to 127.0.0.1 outside containers) and set a long `ADMIN_TOKEN`.
- [ ] `ENABLE_SIMULATOR=false`.
- [ ] Provide `SECRETS_MASTER_KEY` from a secret manager; back it up.
- [ ] Set a spend limit in the Claude Console as a second guard.
