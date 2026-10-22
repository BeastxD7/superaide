# Morning summary, 22 Oct 2026

What was built overnight, what's proven, and what needs you. Detail is in `docs/overnight-log.md` and the ADRs.

## Shipped to main (tested, with CI green on the latest push)

- **Streaming chat:** live reasoning, each tool or skill call, and the answer as it's written; folded into "Worked through N steps" afterwards.
- **Reminders done by Ava herself:** typos cleaned ("Drink water"). She can list, move, reword and cancel them, and send each one to the app, Telegram or Slack. Delivered reminders are listed apart from upcoming ones.
- **Cross-channel memory (ADR 0008):**
  - one timeline across your private chats
  - typo- and spelling-tolerant search, plus local open-source search by meaning (no data leaves the Mac)
  - pinned preferences, and dedupe when you change your mind
  - verified live: "what color do i hate?" in the app → "black, you said so on Telegram [1]"
- **The citation checker:** made-up sources are dropped; unsourced claims are revised or flagged.
- **Pluggable AI providers (ADR 0007):** the *AI model* page with Claude, OpenAI, Azure, OpenRouter, xAI, Gemini, Ollama, vLLM and custom endpoints. Prices are enforced, a Brave web-search key can be added, and there are guards against reaching internal servers.
- **Telegram:**
  - the new bot works
  - a first message to a new bot is no longer dropped as a duplicate
  - proper formatting, with a sources line
  - channel health: "Token rejected" on Connections and an in-app note, never silent
  - links are bound to the bot that confirmed them (a new bot needs "confirm it's you")
  - sensitive actions are blocked from chat
- **Google Calendar card, briefing with your calendar, and meeting prep 15 minutes before each meeting.**
- **Real brand logos, and the Node 24 / Dependabot updates.**
- **VPS deployment** (`deploy/README.md`, `deploy/bootstrap.sh`), tested end to end on the Mac. Not public yet.
- **Many owners (ADR 0009):**
  - tables and email verification and reset (no email service chosen yet)
  - the stranger AI notice, and real time zones at sign-up
  - Row-Level Security policies, and per-owner spend caps (the platform allowance stays $0 until you decide pricing)
  - platform-wide accounts
  - a second owner hiring is behind a switch that's **off**, until credentials, channels, jobs and memory are separated too

## Reliability scorecard (real model, every scenario 3×)

| | Before | After fixes |
|---|---|---|
| Mean | 93% | **97%** |
| Pass all 3 runs | 87% | **94%** |

Citations, approvals and briefing went to 100%. Round 3 (harder tests: injection attempts, long conversations, changes of mind, Hindi and Spanish) was in progress. Total spend about US$0.5.

## Designs written for your review (proposed)

- **ADR 0010, triage & escalation:** answer it, hold it for the digest, or interrupt you, with Acknowledge buttons. Its feedback loop is what makes the trust stages real. The foundations are being built behind a switch that's off.
- **ADR 0011, mobile:** installable app first, with privacy-safe push ("Ava · Needs you"); native only if measured triggers fire. The foundations are being built behind a switch that's off.
- **docs/research/AGI era growth ideas.md:** top ideas are the "Ask my Ava" link, "while you were out", Ava's public report card, the "Break my Ava" challenge, and speaking each sender's language.

## Needs you (decisions)

1. **Your Telegram account:** Telegram reset "suspicious sessions" last night. Check Settings → Devices and turn on Two-Step Verification.
2. ~~Login email typo~~: corrected to shashank@pebbleroad.com (done 10:40).
3. **Restricted database role on your dev DB** (makes Row-Level Security bind locally). Runbook in the ADR 0009 T1 report; your data is backed up at `~/superaide-dev-2026-10-22-0258.dump`.
4. **Pricing and keys:** each owner's own Claude key, a platform key, or both? Is there a free tier?
5. **Telegram for many owners:** each owner's own bot, or Telegram's new managed bots?
6. **Email provider** for verification and resets (Postmark, SES or Resend), and when to require verified email.
7. **Start "Capabilities" with Gmail triage?**
8. **Triage questions** (ADR 0010, 10 of them) and **mobile questions** (ADR 0011, 9 of them).
9. **Which growth idea to try first.**

## Housekeeping

- The phone link (Cloudflare quick tunnel) may still be running. Say if you want it closed.
- A usage limit paused agents from about 04:30 to 05:20; all were resumed.
- At 09:30 GitHub couldn't be reached from the Mac (fetch hung), so this summary may be pushed late.
