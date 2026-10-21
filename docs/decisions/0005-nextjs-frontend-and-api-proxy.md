# 5. Next.js frontend that proxies the API on its own origin

Date: 2026-10-21. Status: accepted. Supersedes the Vite single-page app.

## Context

The first frontend was a Vite single-page app with hash URLs (`/#/agents/ava`) that called the API on another port. Two problems followed:

- **Sign-in broke depending on host name.** When the page was opened at `localhost:3000` but the API was configured as `127.0.0.1:8000`, the browser treated them as different sites and refused to store the session cookie. Users signed up, then were asked to log in again.
- **Public pages couldn't be found.** The marketplace and agent profiles should be indexable and show previews when shared, which a client-only app with hash URLs can't do.

## Decision

- Use Next.js 16 with the App Router, with clean URLs: `/agents/ava`, `/agents/ava/hire`, `/signup`, `/login`, `/meet`, `/workspace`, `/console`.
- Render public pages on the server. Render signed-in screens (camera, speech, live chat) in the browser.
- The browser calls `/api/*` on the frontend's origin. A route handler forwards each request to FastAPI (`API_INTERNAL_URL`), streaming bodies and passing cookies through. It reaches only the API's public prefixes. It drops client-supplied forwarding headers unless `TRUST_PROXY_HEADERS=true`.
- `proxy.ts` sends signed-out visitors from private pages to log in, and back afterwards.
- FastAPI stays the only backend. Next.js holds no business logic.

## Consequences

- The session cookie always belongs to the page's own origin, so the host-name mismatch can't recur. CORS is no longer involved for the app.
- The frontend needs a Node server (standalone output in Docker). `start.command` installs Node when it's missing.
- Every API call takes one extra local hop through the frontend server.
