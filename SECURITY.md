# Security policy

## Reporting a vulnerability

**Never report a vulnerability in an issue, a pull request or a commit message.** Report it privately:

1. **GitHub Security Advisories.** On the repository's **Security** tab, choose **Report a vulnerability** if it is shown. Repository admins can instead open **Advisories → New draft security advisory**, which stays private until it is published.
2. **Otherwise**, contact the maintainer, [@BeastxD7](https://github.com/BeastxD7), directly and ask for a private channel before sending details.

Private vulnerability reporting on GitHub is available for public repositories; while this repository is private, use option 2 unless you are an admin.

Include:

- what you found and where (file, endpoint, channel, commit);
- how to reproduce it, with the smallest example you can;
- what an attacker could do with it, and who would be affected (owners, senders, other instances);
- whether you have shared it with anyone else.

Use made-up data. Don't access, keep or share anyone's real messages or credentials while testing.

### What to expect

| Step | Target |
| --- | --- |
| Acknowledgement | 3 working days |
| First assessment and severity | 7 working days |
| Fix for a critical issue (data leak across owners or audiences, impostor accepted, spend cap bypassed) | 14 days |
| Fix for other issues | Planned with the next releases |

We'll keep you updated, agree on a disclosure date with you, and credit you in the advisory unless you'd rather not be named.

### In scope

- This repository: the backend (`backend/`), frontend (`frontend/`), deployment files (`deploy/`, `docker-compose.prod.yml`) and CI (`.github/`).
- Anything that breaks the security model below. These matter most:
  - one owner's data visible to another instance (row-level security, ADR 0009);
  - a sender learning something the owner didn't share (the audience gate);
  - an impostor recognised as someone else, or a channel linked without the one-time code;
  - an action taken without the approval it needs, including through prompt injection in untrusted content;
  - spend past the cap, or a model call while frozen;
  - credentials leaving the vault (logs, responses, prompts).

### Out of scope

- Vulnerabilities in third-party services (Anthropic, Telegram, Slack, Google). Report those to the vendor.
- A model saying something odd that discloses nothing and takes no action. Open a normal issue.
- Findings that need a compromised machine, a leaked `SECRETS_MASTER_KEY` or `ADMIN_TOKEN`, or a deployment that ignores the checklist below.
- Missing hardening headers or rate limits without a demonstrated impact.

## Security model

- **Identity.** People are recognised by immutable platform IDs, never by display names or what a message claims. A new channel joins a person only through a one-time code sent to a channel already trusted (6 digits, 10-minute expiry, locked after 5 wrong tries). Every link is audited and reversible.
- **What the assistant may reveal.** Memories carry a visibility. The audience gate filters them in SQL to what everyone in the conversation may see, before the prompt is built. The owner brief given to non-owners contains only what the owner chose to share.
- **Actions.** The model can only propose actions. Code decides from stored roles, and sensitive actions need the owner's approval.
- **Instances.** Each owner's Ava is a separate instance. Every per-instance row carries `agent_id`, and Postgres row-level security keeps instances apart even if a query forgets to filter (ADR 0009).
- **Untrusted content.** Web pages, emails, files and community tools are data, never instructions. A turn that read them is tainted and can't take a side effect without an explicit approval (ADR 0006).
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
- [ ] Expose only the Next.js server; keep the API on a private network (`API_INTERNAL_URL`).
- [ ] Set `TRUST_PROXY_HEADERS=true` on the frontend only behind a load balancer that overwrites `X-Forwarded-For`, and limit the API's `FORWARDED_ALLOW_IPS` to the frontend's address.
- [ ] Set `FRONTEND_ORIGIN` to the real app origin.
- [ ] Keep the admin API private (it binds to 127.0.0.1 outside containers) and set a long `ADMIN_TOKEN`.
- [ ] `ENABLE_SIMULATOR=false`.
- [ ] Provide `SECRETS_MASTER_KEY` from a secret manager; back it up.
- [ ] Set a spend limit in the Claude Console as a second guard.
