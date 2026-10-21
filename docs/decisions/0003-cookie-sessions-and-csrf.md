# 3. Server-side sessions in an httpOnly cookie

Date: 2026-10-21. Status: accepted.

## Context

The app needs signed-in users and the ability to sign a device out. The frontend and API run on different ports of the same host.

## Decision

- A random 256-bit session token in an httpOnly, SameSite=Lax cookie. Only its SHA-256 is stored, with a sliding 30-day expiry and server-side revocation.
- No JWTs.
- State-changing requests must send `X-Requested-With`. CORS allows credentials only from `FRONTEND_ORIGIN`.
- The frontend defaults the API host to the page's own host name, so the cookie stays first-party.

## Consequences

- Logging out, "log out everywhere" and password resets take effect immediately.
- Scripts can't read the token, so cross-site scripting can't steal it.
- Behind HTTPS in production, set `SESSION_COOKIE_SECURE=true`.
