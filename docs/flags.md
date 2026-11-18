# Feature flags

Generated from [`backend/flags.toml`](../backend/flags.toml) by `scripts/check_flags.py --write`; do not edit by hand.
70 flags (budget 70, target 15 owner-facing). A flag that is not `stable` has a remove-by date: it is promoted or deleted by then.

| Flag | Default | Stage | Owner | Remove by | What it does |
| --- | --- | --- | --- | --- | --- |
| `ACTION_LOG` | false | experiment | maintainer | 2027-02-14 | hash-chained log of tool calls and executed approvals |
| `AIRLOCK_ENABLED` | true | stable | maintainer |  | replace other people's contact details, ids, addresses and names with placeholders before a model call |
| `ALLOW_MANY_OWNERS` | true | stable | maintainer |  | anyone who signs up may hire their own Ava (false = single-owner mode) |
| `ALLOW_UNSAFE_PUBLIC_SELF_HOSTED` | false | experiment | maintainer | 2027-02-14 | override the startup refusal of a public open-sign-up self-hosted server (do not) |
| `APPROVAL_METRICS_ENABLED` | true | stable | maintainer |  | count and time approval cards (no text or names): the owner's 'How much am I being asked?' card and a weekly table in the console |
| `APPROVAL_REQUIRE_EXECUTOR` | true | stable | maintainer |  | refuse an approval nothing can carry out (and web-form/contact-page routes) instead of queueing it |
| `ASK_LINK_ENABLED` | false | experiment | maintainer | 2027-02-14 | public /a/<handle> intake pages owners can publish (public, unauthenticated) |
| `AUDITION_ENABLED` | false | experiment | maintainer | 2027-02-14 | compare 2 or 3 candidate skills side by side on the last tasks (no network, effects stubbed) |
| `BRAIN_SHARE_ENABLED` | false | experiment | maintainer | 2027-02-14 | owner-created unlisted link (max 7 days, revocable) to a counts-only picture of memory |
| `CIRCUIT_BREAKER_ENABLED` | false | experiment | maintainer | 2027-02-14 | too many deletes or sends in a window freezes Ava; the halt word freezes her at once |
| `CONNECTOR_FACTS_ENABLED` | false | experiment | maintainer | 2027-02-14 | keep up to 3 short facts per calendar event or verified email as notes (small-model calls, never the raw text) |
| `DISSENT_DESK_ENABLED` | false | experiment | maintainer | 2027-02-14 | three different models answer high-stakes choices (needs DISSENT_MODELS; costs more) |
| `ECHO_FILTER_ENABLED` | false | experiment | maintainer | 2027-02-14 | facts heard via links, Telegram or Slack are hearsay until confirmed |
| `ENABLE_API_DOCS` | true | stable | maintainer |  | interactive API docs at /docs (turn off on a public server) |
| `ENABLE_SIMULATOR` | false | experiment | maintainer | 2027-02-14 | operator console impostor simulator (development only; off unless you turn it on) |
| `FAKE_LLM` | false | experiment | maintainer | 2027-02-14 | true forces offline echo replies and nothing is spent (tests, demos) |
| `FOOTPRINT_GUARD_ENABLED` | false | experiment | maintainer | 2027-02-14 | creating accounts, profiles or public posts needs approval; flags unknown names |
| `FORK_NIGHT_ENABLED` | false | experiment | maintainer | 2027-02-14 | three read-only overnight drafts of a choice the owner leaves (model calls) |
| `GMAIL_TRIAGE_ENABLED` | false | experiment | maintainer | 2027-02-14 | poll each owner's Gmail inbox and triage new mail (needs TRIAGE_ENABLED, Google) |
| `GOOGLE_CALENDAR_PUSH` | false | experiment | maintainer | 2027-02-14 | Google calendar change notifications (needs PUBLIC_BASE_URL on https) |
| `GOOGLE_SIGNIN_ENABLED` | false | experiment | maintainer | 2027-02-14 | "Continue with Google" on log-in and sign-up (needs Google client id and secret) |
| `IDENTITY_CLAIM_REVIEW` | false | experiment | maintainer | 2027-02-14 | an unknown account named like a contact is a stranger; the owner is asked who they are |
| `IMPORT_LEASE_ENABLED` | false | experiment | maintainer | 2027-02-14 | import context cards from a third-party export under a time-limited lease |
| `MAIL_LOG_BODY` | true | stable | maintainer |  | with MAIL_BACKEND=log, log whole emails (links included); false = recipient and subject only |
| `MANDATES_ENABLED` | true | stable | maintainer |  | signed mandate cards (can be handed on only narrower; revoking one revokes its children) let Ava do a named limited thing without asking |
| `MANDATE_SHADOW_ENABLED` | true | stable | maintainer |  | record (never act on) whether a mandate would have covered each approval |
| `MANDATE_SKIP_ENABLED` | true | stable | maintainer |  | a covering signed mandate lets Ava add or move a no-guest calendar event without a card (Child stage up, untainted, after shadow data) |
| `MCP_PINNING` | false | experiment | maintainer | 2027-02-14 | block an MCP tool whose description or schema changed since the owner approved it |
| `MEMORY_RECHECK_ENABLED` | false | experiment | maintainer | 2027-02-14 | digest may ask "is this still true?" about a few lasting facts a week |
| `MEMORY_SOCKET_ENABLED` | false | experiment | maintainer | 2027-02-14 | read-only MCP endpoint POST /mcp/memory for the owner's other assistants |
| `MIRROR_ENABLED` | false | experiment | maintainer | 2027-02-14 | "The Mirror": a weekly note of patterns in the owner's own reminders and messages (owners also opt in) |
| `MODEL_DRILL_ENABLED` | false | experiment | maintainer | 2027-02-14 | replay the last tasks on two models and show where the call sequences differ |
| `NOTIFY_FAILOVER` | true | stable | maintainer |  | a push that stays down is sent on the owner's other linked channel; reception notes follow their interrupt preference |
| `ONE_GOOD_QUESTION_ENABLED` | false | experiment | maintainer | 2027-02-14 | Ava may ask the owner one clarifying question a day |
| `OVERNIGHT_TUNER_ENABLED` | false | experiment | maintainer | 2027-02-14 | proposes a better triage prompt; nothing changes until the owner approves |
| `OWNER_TWIN_ENABLED` | false | experiment | maintainer | 2027-02-14 | calibration page scoring a predictor of the owner's approve/reject choices |
| `PAYOUTS_ENABLED` | false | experiment | maintainer | 2027-02-14 | creator payout LEDGER per accepted outcome with clawback and bond; moves no money (null provider only) |
| `PIPELINE_TIMING_ENABLED` | true | stable | maintainer |  | time each stage of every message turn (milliseconds only, no text); p50 and p95 in the operator console |
| `PLATFORM_AI_ENABLED` | false | experiment | maintainer | 2027-02-14 | owners may use the server's own Claude key as "Superaide AI credits" (costs you) |
| `PRINCIPAL_CARD_ENABLED` | true | stable | maintainer |  | every email Ava sends is signed 'Ava, personal assistant of <first name>' with a link to a signed page (owner can untick per email); false = kill switch |
| `PUSH_ENABLED` | false | experiment | maintainer | 2027-02-14 | web push to the owner's phone (makes a VAPID key pair on first use) |
| `QUOTE_ODDS_ENABLED` | false | experiment | maintainer | 2027-02-14 | approval cards show honest odds; low-odds approvals prepare instead of run |
| `REHEARSAL_ENABLED` | false | experiment | maintainer | 2027-02-14 | practise a hard conversation with Ava playing the other side |
| `RELATIONSHIP_SAFETY_ENABLED` | false | experiment | maintainer | 2027-02-14 | fixed replies for heavy or "do you love me?" owner messages |
| `REPLAY_BEFORE_UPDATE_ENABLED` | false | experiment | maintainer | 2027-02-14 | replay the last tasks with and without a skill, prompt or model change, then approve or reject |
| `REQUIRE_VERIFIED_EMAIL` | false | experiment | maintainer | 2027-02-14 | hiring needs a confirmed email (turn on once MAIL_BACKEND=smtp works) |
| `RUBBER_STAMP_BRAKE_ENABLED` | false | experiment | maintainer | 2027-02-14 | adds friction to fast always-yes approvals (can only add friction) |
| `RUN_CHANNELS_IN_API` | true | stable | maintainer |  | Telegram and Slack listeners inside the API process |
| `RUN_JOBS_IN_API` | true | stable | maintainer |  | reminders, follow-ups, digests inside the API process |
| `SCHOOL_ENABLED` | false | experiment | maintainer | 2027-02-14 | Ava School: write, consent to and export generic lessons (local only) |
| `SCHOOL_PLAYBOOK_ENABLED` | false | experiment | maintainer | 2027-02-14 | owner-written playbook text per lesson, put in the prompt only after its eval passed |
| `SCHOOL_SHARING_ENABLED` | false | experiment | maintainer | 2027-02-14 | signed lesson files, only for categories the owner allows (needs SCHOOL_ENABLED) |
| `SECOND_OPINION_ENABLED` | false | experiment | maintainer | 2027-02-14 | memory-free second view on the owner's own claims (small-model calls) |
| `SESSION_COOKIE_SECURE` | false | experiment | maintainer | 2027-02-14 | send the session cookie over HTTPS only (must be true on any https server) |
| `SKILL_COOLDOWN_ENABLED` | false | experiment | maintainer | 2027-02-14 | a new skill version waits 0/24/72 h by effect class before installs get it; deleted names are reserved |
| `SKILL_RATCHET_ENABLED` | false | experiment | maintainer | 2027-02-14 | Ava drafts playbooks; only owner-edited ones go live |
| `SKILL_RECEIPTS_ENABLED` | false | experiment | maintainer | 2027-02-14 | content-free, hash-chained receipts of how you answered a skill's actions; tiers computed from them |
| `SKILL_SIGNALS_ENABLED` | false | experiment | maintainer | 2027-02-14 | several scanners per skill (routed by their disagreement), a runtime-built behaviour card, demotion on a manifest gap |
| `SLEEP_JOB_ENABLED` | false | experiment | maintainer | 2027-02-14 | nightly job: episodes, and tidying duplicate and contradicting notes (small model, capped) |
| `SURFACE_CHECK_ON_BOOT` | false | experiment | maintainer | 2027-02-14 | refuse to start if a protected file differs from security/surface.lock.json |
| `TASK_PROTOCOL_ENABLED` | true | stable | maintainer |  | errands need a read-first plan (goal, evidence, route, ability, recommendation) before an approval |
| `TIME_TRAVEL_RULES_ENABLED` | false | experiment | maintainer | 2027-02-14 | standing orders compiled to rules, replayed on past messages, shadow-run first |
| `TRACE_RECORDER` | true | stable | maintainer |  | hashes-only record of every tool call (on by default; turn off only to save rows) |
| `TRIAGE_ENABLED` | false | experiment | maintainer | 2027-02-14 | record a handle/hold/interrupt/defer/verify decision for non-owner messages |
| `TRIAGE_KNN_ENABLED` | false | experiment | maintainer | 2027-02-14 | shadow urgency score beside each triage decision (needs TRIAGE_ENABLED) |
| `TRIAGE_KNN_RAISE_ENABLED` | false | experiment | maintainer | 2027-02-14 | once YOU press Promote, the kNN head may raise (never lower or silence) a calm message's urgency (needs TRIAGE_KNN_ENABLED) |
| `TYPING_INDICATORS` | true | stable | maintainer |  | "typing..." on Telegram, a status or "Working on it..." placeholder on Slack while Ava replies |
| `VOICE_INTRO_V2` | false | experiment | maintainer | 2027-02-14 | conversational voice interview in the introduction call |
| `WEB_TOOLS_ENABLED` | true | stable | maintainer |  | Ava may search and read the web (about US$0.01 a search from the research budget) |
| `WHY_REPLAY_ENABLED` | false | experiment | maintainer | 2027-02-14 | "Why did you say that?" replay of a reply (small-model calls) |
