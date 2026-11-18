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

## Addendum, 18 November 2026: what the proxy costs, measured (#419)

An outside review called the `/api` proxy unnecessary overhead. The decision above was argued but not measured, so it is measured here. Nothing about the decision changes.

**Why the proxy stays.** It is the reason the session cookie is first-party: the browser only ever talks to the page's own origin, so the `localhost` versus `127.0.0.1` sign-in failure cannot come back, and CSRF (ADR 0003) and the Content-Security-Policy are written for one origin instead of two. It also keeps the API's admin, docs and webhook routes off the public address (only `auth`, `app`, `api`, `health` and `p` are forwarded) and is the one place that decides which forwarding headers to trust.

**How it was measured.** `e2e/scripts/proxy-bench.mjs` sends the same requests straight at a no-op upstream (`e2e/scripts/fake-upstream.mjs`) and through the production build of the Next server in front of it, so the difference is the proxy hop alone: no FastAPI time, no database. 2,000 requests per kind after a 100-request warm-up, keep-alive connections, Node 22, Next 16.4, on an Apple M5 Pro (15 cores), everything on loopback. Run it with `npm run build` in `frontend/`, then `node e2e/scripts/proxy-bench.mjs 2000 1` (the second number is the concurrency).

One request at a time (milliseconds, p50 / p95):

| Request | Direct | Through the proxy | Added |
| --- | --- | --- | --- |
| GET, small JSON | 0.23 / 0.52 | 1.61 / 2.86 | 1.4 / 2.3 |
| GET, headers echoed back | 0.09 / 0.18 | 1.16 / 1.98 | 1.1 / 1.8 |
| POST, 200 B body | 0.11 / 0.21 | 1.28 / 1.93 | 1.2 / 1.7 |
| POST, 20 KB body | 0.24 / 0.38 | 1.48 / 2.57 | 1.2 / 2.2 |

So the proxy adds about **1.1 to 1.4 ms at p50 and under 2.5 ms at p95** per call. For scale, a chat reply waits seconds for the model, and a browser round trip over a real network is tens of milliseconds.

Ten requests in flight (a single Node process is both the proxy and, here, the load generator, so this is a worst case): p50 added 8 to 10 ms, p95 12 to 18 ms. That is queueing on one core (about 1 ms of work per request), not a longer hop. One proxy process therefore handles on the order of a thousand requests a second; the app is a single-owner assistant, far below that.

**Streaming.** A two-event stream (the second event 1.5 s after the first) arrives in two separate chunks, at 3 ms and 1,504 ms direct and 5 ms and 1,509 ms through the proxy: it is passed through, not buffered. The proxy contract tests cover it (`E2E_PROXY=1 npx playwright test --project=proxy`, 26 passed, including "an event stream is passed through as it arrives" and a 3 MB multipart upload).

**Memory.** The Next server sat at about 105 MB resident before the load and 95 MB after one-at-a-time traffic (141 MB after the ten-at-a-time run). That process exists anyway to render pages, and its memory with and without the proxy route was not measured separately, so no per-proxy figure is claimed.

**Limits of this measurement.** Loopback only, against a fake upstream: it isolates the hop and says nothing about FastAPI's own latency or about a hosted network. It was not run against a hosted deployment; do that before quoting hosted numbers.

**When to revisit.** Reopen this decision if any of these hold: the hop adds more than 10 ms at p95 in a hosted run under expected load; the Next server becomes CPU-bound by API traffic rather than page rendering; streaming is found buffered in production behind a CDN or load balancer (a deployment problem, but it would defeat the chat); or the web app and the API are put on one registrable domain and cookies, CSRF and CSP stop needing a single origin. Removing the proxy is a separate piece of work and is out of scope here.
