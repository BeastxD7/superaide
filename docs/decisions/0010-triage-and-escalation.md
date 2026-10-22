# 0010: Triage and escalation

Status: partially built (flag off), 2026-10-22. TR0–TR3 and TR7–TR10 are on main behind `TRIAGE_ENABLED`
(default false); see "As built" below. The open questions are still open.

## Context

Superaide's core promise is the one line in the catalog: Ava "answers for you when you're busy, and only
interrupts you when it really matters" (`app/catalog.py:20`). The owner is a busy person who is the bottleneck
for their own inbound. For every message from someone else, Ava has to choose one of three things:

- **(a) Handle**: answer it herself, within what the owner allowed.
- **(b) Hold**: keep it for the digest.
- **(c) Interrupt**: tell the owner now, and escalate until they explicitly acknowledge it.

The product vision lists triage and escalation as roadmap items 4 and 5, not built, and calls them the
biggest gap (`.delivery/product-vision.md`). The trust stages (Infant → Child → Adolescent → Adult) are a
label today. This ADR is meant to make them real, and to give them their first measured gate.

### What the code does today

Paths are relative to `backend/src/superaide/`.

| Where | Today | Gap |
|---|---|---|
| `pipeline.py` `Agent.handle` | Verify → resolve → context → dedupe → freeze → invite/stranger → owner branches → `_think` for **every** known non-owner, DM or group | No triage step. Every colleague gets a model reply, and the owner is never told. |
| `pipeline.py` `_stranger` | Fixed reply, no model call, no memory | Strangers with a real deadline have no way in (see the research below). |
| `policy/actions.py` | `decide` gates *actions* (approval or refused). `EXECUTORS` is empty. | Doesn't decide anything about attention. No stage input. |
| `proactive/notify.py` `notify_owner` | Always writes to the app; also pushes to `interrupt_channel` (Telegram only through `"default"`); notes failed deliveries | One shot: no ack, no timer, no second rung, no buttons. `"default"` ignores `slack`. |
| `proactive/digest.py` `compose` | Calendar, reminders, dates and a count of pending approvals; built in code at no cost | Knows nothing about inbound messages. Has no "needs you", "handled" or "held" sections. |
| `knowledge/profile.py` | `in_quiet_hours`; the owner brief tells *the model* about urgent topics and VIPs | The rules are only prompt text. No code consumes `urgent_topics`, `Contact.vip` or `vip_breaks_quiet`. |
| `db/models.py` `OwnerPreference` | `urgent_topics`, `digest`, `interrupt_channel`, `quiet_start`/`quiet_end`, `vip_breaks_quiet`, `timezone` | No ladder, interrupt budget, silenced senders, away mode or autonomy grants. |
| `channels/telegram.py` `parse_update` | Reads `update["message"]` only | `callback_query` updates (button presses) are dropped. |
| `channels/slack.py`, `slack_manifest.yml` | Handles `message` and `app_mention` events. `interactivity.is_enabled: false` | No `block_actions` handler, so buttons can't work. |
| `channels/base.py` `Messenger` | `send_dm(identity, text)` and `send_to_context(...)` | No way to attach buttons, or to edit a sent message after an ack. |
| `jobs/queue.py` | Postgres queue: `SKIP LOCKED`, `dedupe_key`, retries; handlers must be idempotent | Fits escalation timers as they are. `dedupe_key` is global until ADR 0009 T5. |
| Stage | `"Infant"` hard-coded in `app/routes.py:192`, `api/admin.py:90`, `brain/turn.py:47`; placeholder gates at `api/admin.py:101` | Nothing measures anything. |

### Research

The repo's own reports already worked out most of the decision logic. This ADR adopts them, cites them,
and spends its length on what they leave open: schema, state machine, the pipeline hook, the feedback
loop, gates and work packages.

- **`Personal assistant memory architecture.md`, "Haiku gathers evidence, code decides".** This report
  supplies the ordered decision table (silenced → risk flag → VIP → urgent topic → defer → take a message
  → handle → needs decision → budget), the classifier output (fixed labels plus verbatim evidence quotes
  that code checks) and the ladder timers (minutes, not PagerDuty's 30). It also sets the rule that
  "seen" never counts as acknowledged, and the digest order with "should have interrupted" and "not
  urgent" on every item.
- **`Ava skills tools and cited memory.md`, "Right data at the right time".** This report has a
  `route()` utility rule (importance × time-criticality × confidence, minus an interruption cost), a
  `nudge_log`, and target numbers: interrupt precision ≥ 0.5, at most one false interrupt a day, a miss
  rate, and the mute rate as an alarm.
- **`Humanistic assistant architecture review.md`.** It maps the stages to Hyatt's five delegation
  levels (Infant L1–L2 observe and report, Child L3 recommend, Adolescent L4 decide and tell afterwards,
  Adult L5) and sets the gate rules: measured, hysteresis, instant demotion, no self-promotion.
- **`AGI era growth ideas.md`.** Idea 2 ("While you were out") is "the first user-facing form" of
  triage and escalation and "should be built as those items, not beside them". It defaults the away
  notice to colleagues only. Idea 11 (the phone rung) uses VIP plus keyword, DTMF ack, a weekly cap, and
  "was this worth a call?" feeding precision.

External practice (checked 22 Oct 2026):

| Product / source | What it does | What we take |
|---|---|---|
| Gmail Priority Inbox ([Aberdeen et al., Google Research](https://research.google/pubs/the-learning-behind-gmail-priority-inbox/)) | A per-user model predicts whether the user will act on a message. It combines social, content, thread and label features with a global model. Later "much more responsive to your manual corrections" ([Gmail blog](https://gmail.googleblog.com/2010/12/priority-inbox-update-time-savings-new.html)). | Global rules first, then **per-owner thresholds learned from corrections**. Not a per-owner model: too little data. |
| Gmail AI Inbox, Jan 2026 (secondary: [Search Engine World](https://www.searchengineworld.com/gmail-gets-ai-treatment-with-gemini-inbox-features), [MindStudio](https://www.mindstudio.ai/blog/what-is-google-ai-inbox-smart-prioritization)) | "Priorities" on top, "Catch me up" summary below; the user can say an item was wrongly prioritised. Drafts, never sends. | The "Needs you" / "Handled" / "Held" split. Corrections are first-class. |
| Superhuman Auto Labels and Split Inbox ([Superhuman](https://blog.superhuman.com/solving-customer-success-email-overload/), [EA help](https://help.superhuman.com/hc/en-us/articles/46005779957261-Executive-Assistants)) | Important = real people; marketing and cold pitches are auto-labelled away. The user can write labels as short prompts. | Silence automated or cold-pitch senders by rule before any model call. Owner-written rules in plain words become typed rules later. |
| Shortwave ([docs](https://shortwave.com/docs/guides/labels)) | AI filters in plain English. Delivery schedules per label or contact (batching). | Per-contact "hold until digest", and the digest as a batching schedule. |
| Slack VIPs ([Slack help](https://slack.com/help/articles/34963579361683)) | Up to 300 VIPs; "Always allow notifications from VIPs" even in Do Not Disturb. | `Contact.vip` + `vip_breaks_quiet` is the right model; it needs a consumer. |
| Apple Focus, Reduce Interruptions ([Apple](https://support.apple.com/guide/mac-help/mchl613dc43f/)) | Model-based breakthrough, but "notifications you explicitly allow or silence are always handled that way". | **Explicit rules beat the model**, both ways. |
| iOS 26 Call Screening ([PIRG](https://pirg.org/articles/call-screening-in-iphone-ios26-nice-effort-to-combat-robocalls-but/); [iGeeksBlog](https://www.igeeksblog.com/call-screening-iphone-ios-26), secondary), Pixel Call Screen ([Google](https://support.google.com/phoneapp/answer/9118387)) | Asks an unknown caller for name and reason, then shows a transcript. PIRG notes callers needn't answer, and some important calls didn't get through. | Take-a-message for strangers asks "what is it and by when?". Never infer urgency from the claim alone. |
| AI receptionists (Smith.ai; vendor-written: [GetVoIP](https://getvoip.com/blog/smith-ai-vs-ruby/), [My AI Front Desk](https://www.myaifrontdesk.com/blogs/how-smith-ai-revolutionizes-customer-service-with-ai-powered-receptionists)) | Take name, contact and reason, and escalate keyword or complex calls to a human in real time. | Structured message-taking. Claims are self-reported, so we don't copy any number. |
| PagerDuty escalation policies ([docs](https://support.pagerduty.com/docs/escalation-policies)) | Rules notify in order; the default timeout is 30 min; repeat up to 9 times; acknowledging stops escalation; a separate ack timeout can re-trigger. | The state machine: `open → notified(step n) → acked → resolved`, with timeouts and a repeat cap. |
| Opsgenie "seen stops escalation" pitfall ([Atlassian community](https://community.atlassian.com/forums/Opsgenie-questions/Why-does-opsgenie-skip-rule-if-user-saw-alert/qaq-p/2512917)) | Viewing an alert suppressed later rules. | Read receipts and opening the app are **never** acks. |
| ProactiveBench ([Lu et al., ICLR 2025, arXiv 2410.12361](https://arxiv.org/abs/2410.12361)), ProAgentBench ([arXiv 2602.04482](https://arxiv.org/html/2602.04482v1)) | Models help when help is needed but "fail to stay silent" (high false alarms). Best when-to-assist accuracy 64.4%; GPT-4o-mini had 96.2% recall at 52.7% precision. | The model must not decide *whether* to interrupt alone. It provides evidence, and code and thresholds decide. |
| Interruption research (Iqbal & Horvitz CHI'07, Iqbal & Bailey CHI'08, Mark CHI'08, Horvitz bounded deferral, Fitz et al. batching; all cited in the memory report) | Interruptions cost 10–25 min; deferring to breakpoints lowers frustration; batching three times a day helps, while no alerts raises anxiety. | Few interrupts, bounded deferral, and two or three digests a day. |
| LLM urgency calibration ([Xiong et al.](https://arxiv.org/html/2306.13063v2), [PMR-Bench](https://arxiv.org/html/2601.13178v1)) | Verbal confidence is overconfident (mostly 80–100%). Agreement across samples is a far better signal. | Treat `confidence` as a hint. For borderline items, resample and use agreement. |
| Telegram inline keyboards ([Bot API](https://core.telegram.org/bots/api#inlinekeyboardbutton); the 64-byte limit is confirmed in [python-telegram-bot](https://docs.python-telegram-bot.org/en/v20.2/telegram.inlinekeyboardbutton.html), secondary) | `callback_data` is 1–64 bytes. The client spins until `answerCallbackQuery`. | An opaque short token in `callback_data`; answer the callback every time. |
| Slack interactivity ([Slack Java SDK guide](https://docs.slack.dev/tools/java-slack-sdk/guides/interactive-components)) | A `block_actions` payload with `action_id`/`value`. The app must `ack()` within 3 s. Socket Mode needs no public URL. | Ack first, then enqueue the work. Enable interactivity in the manifest. |

## Decision

### 1. Where triage sits in the pipeline

The triage step goes in `Agent.handle` after verify, resolve, context, dedupe and the freeze check, and
after the invite and stranger branches. It runs for **every inbound whose sender is not the owner**: a DM
to Ava from a known person, or a group message that addresses Ava. It replaces the unconditional
`_think` call. The owner's own turns never go through triage.

```
inbound (not the owner)
  → features (code)          who, relation, VIP, contact, channel, chat kind, thread state, repeats,
                             quiet hours, away, interrupt budget left, stage
  → hard rules (code)        R1–R9 below; may decide outright
  → classifier (small model) only if rules didn't decide, and only within the triage budget
  → policy (code)            decision = f(rules, classifier, thresholds[owner], stage)
  → act                      handle | hold | interrupt | verify ; tell the sender a fixed template
  → record                   triage_decision row + audit
```

Two cases keep today's behaviour:

- **The owner in a group** still goes to `_think` as today, not to triage.
- **Group messages that don't address Ava** still reach `handle` today. They get a recorded decision
  (mostly `hold`/FYI) and **no** sender template. In `mixed_external` contexts the only template ever
  sent is the hold row, and only when Ava was addressed.

`_think` becomes the *handle* path. It runs only when the decision is `handle`, and its reply goes
through approval at Infant/Child (section 6). A `hold` or `interrupt` decision sends the sender a fixed,
code-chosen acknowledgement (section 9), not a model reply.

**Strangers stay as they are by default.** That means a fixed reply, no model call and no memory. The
research proposes take-a-message with a tool-less classifier call for strangers. That would change a
standing rule (no model call for strangers) and open a cost surface, so it becomes an **owner setting,
`stranger_intake`, off by default**. When it's on, the stranger reply asks "What is it about, and by
when?". Their next message is classified with no memory and no tools, under a per-sender cap (3 a day)
and a daily stranger budget. A stranger can never reach an interrupt except through an urgent-topic
match that passes the risk checks, and never above `verify`. See open question 1.

### 2. Rules first (code)

Rules are evaluated in this order. The first that decides wins, and every rule that matched is recorded
as `rule_hits`.

| # | Rule | Outcome |
|---|---|---|
| R1 | Kill switch frozen | Nothing (as today) |
| R2 | Sender or topic silenced by the owner (`silenced_people`, `silenced_topics`) | `hold` (digest), never interrupts |
| R3 | Risk screen (regex/keyword plus classifier flags): payment or bank-detail change, gift cards, credentials or OTP codes, "keep this between us", "bypass", a claimed identity that doesn't match the resolved Person, embedded instructions to Ava | `verify`: never handled autonomously. The owner sees "unverified request" and the item is only interrupt-worthy if it otherwise would be. |
| R4 | Automated or bulk sender (bots, `kind = agent`, no-reply patterns) | `hold`, FYI section |
| R5 | VIP (`Contact.vip`) on a **verified** identity (trust ≥ LINKED), with an urgent signal (urgent topic, a deadline within 2 h, or classifier urgency ≥ high) | `interrupt`; breaks quiet hours if `vip_breaks_quiet` |
| R6 | Repeat breakthrough: the same verified person sends ≥ 2 messages within 3 min that mention urgency (the iOS "calls twice" rule) | Treated as an urgent signal for R5/R7. Unverified repetition never counts. |
| R7 | Urgent-topic match (`urgent_topics`, matched by keyword in code or by topic id from the classifier) from a known sender | `interrupt` if urgency ≥ high, else `hold` flagged "maybe urgent" |
| R8 | Quiet hours or away, and not R5 | `interrupt` becomes `defer`, bounded by the deadline or the end of quiet hours, whichever comes first |
| R9 | Interrupt budget used up (`max_interrupts_per_day`, default 6; per hour, default 2) | Only verified VIP + critical gets through; the rest are deferred to the next breakpoint |

The rules use only typed data: `Contact`, `OwnerPreference`, `ChannelIdentity` trust, and the new
`triage_rule` rows from corrections (section 7). The owner's free-text `never_share` is **not** a
triage input. VIP status attaches only to an authenticated channel identity. A VIP's display name on an
unlinked account is an impersonation signal (R3), not a boost.

### 3. The classifier (small model, strict schema)

The classifier runs only when the rules didn't decide. It is one call with no tools, no memory and no
owner brief. The prompt holds only:

- the message (plus up to 2 earlier messages from the same thread, from the same sender only);
- the sender's relation label ("colleague", "VIP", "known contact"), not their history;
- the owner's urgent topics as numbered ids, and the list of answerable intents the owner granted
  (section 6).

```python
class TriageOutput(BaseModel):            # → JSON Schema with additionalProperties: false
    model_config = ConfigDict(extra="forbid")
    category: Literal["question", "request", "scheduling", "fyi", "social", "sales", "other"]
    urgency: Literal["low", "normal", "high", "critical"]
    deadline_text: str = ""               # verbatim, e.g. "by 5pm today"; code parses it
    needs_owner: bool                      # a decision only the owner can make
    can_answer: Literal["no", "from_notes", "with_action"]
    answer_intent: str = ""                # must be one of the granted intent ids, else ignored
    matched_topics: list[str]              # urgent-topic ids; unknown ids are dropped
    risk_flags: list[Literal["payment", "credentials", "secrecy", "impersonation", "instructions", "legal_hr"]]
    evidence: list[str]                    # ≤ 3 verbatim quotes from the message
    confidence: float                      # 0..1, range-checked in Pydantic, not in the schema
    reason: str                            # ≤ 200 chars, shown to the owner only
```

These rules are enforced in code:

- `deadline_text` is parsed in code with `intentions/when.py` `find_moment`, in the owner's time zone;
  if it doesn't parse, there's no deadline.
- Every `evidence` quote must be a substring of the raw message after whitespace normalisation. If any
  quote fails, the output is discarded and the item falls back to `hold` with reason "couldn't
  classify". `reason` is shown only to the owner, with the verified quotes.
- Numeric ranges live in Pydantic, not the schema (Haiku's structured outputs reject min/max). No
  `temperature`, `top_p` or `top_k` is sent to Haiku 5.5.
- **Borderline items** (decision would flip between `hold` and `interrupt` within ±0.1 of the
  threshold) are resampled twice more. The agreement rate replaces `confidence` in the policy. This
  roughly triples the cost of ~10–20% of items.
- **No model connected** (offline / `FakeLLM` without a script): rules only. Anything not decided by
  the rules is held, so triage never blocks and never costs anything offline.
- **Budget**: spend purpose `triage` in its own daily bucket (`BUCKETS["triage"] = "triage"`). If the
  bucket is exhausted, fall back to rules only (hold) and audit `triage_budget_fallback`. A refusal in
  triage must never silently drop a message.

### 4. Policy decides, and the stage caps the model

`triage/policy.py` turns features + rule hits + classifier output + the owner's thresholds + stage into
one decision: `handle | hold | interrupt | defer | verify`. A score in the spirit of `route()` decides
only items the rules left open:

```
score = w_u[urgency] * time_factor(deadline) * (agreement or confidence)
interrupt if score ≥ θ_interrupt[owner]           (start 0.6)
handle    if can_answer != "no" and intent granted and no risk flags and not needs_owner
hold      otherwise
```

The stage caps what the *model* can cause on its own. A rule match (R5/R7) can interrupt at any stage,
because rules are the owner's explicit instructions.

| Stage | Interrupt from model score alone | Handle (reply without the owner) | Hold |
|---|---|---|---|
| **Infant** | No. A model-only "interrupt" becomes `hold` + "maybe urgent" at the top of the Needs-you inbox, with an in-app note (rung 0) and no push. | No. Each drafted reply is an `Approval` (`reply_on_behalf`); the sender gets "I've passed this on". | Yes |
| **Child** | Yes, rung 0–1 only (in-app + one channel push), if score ≥ θ + 0.1 | Drafts approved per message; one-tap approve from the push | Yes |
| **Adolescent** | Yes, full ladder except the phone | Granted intents run without approval, with undo and a "Handled for you" digest entry; anything else is approval | Yes |
| **Adult** | Yes, including the phone rung for critical | Granted intents plus scheduling for known contacts; escalates by exception | Yes |

`verify` (R3) is the same at every stage: never handled, always shown to the owner as unverified.

### 5. The escalation ladder

An `interrupt` (or a `defer` that comes due) creates an `escalation`. The ladder is per owner (stored on
`triage_settings`). Defaults:

| Step | When | Where | Content |
|---|---|---|---|
| 0 | t = 0 | In-app: the Needs-you inbox, plus web push when the PWA exists | One-line reason, verified quote(s), the sender's name; buttons |
| 1 | t = 0 if `interrupt_channel` ≠ app, else +3 min | `interrupt_channel` (Telegram or Slack) with buttons | Same; the lock-screen-safe preview is the reason line only |
| 2 | +5 min after step 1 without ack | The other linked channel (Slack ↔ Telegram) | Same, marked "2nd try" |
| 3 (later, Adult + critical only) | +10 min after step 2 | Phone call to the owner's verified number, fixed text plus DTMF (growth idea 11) | Not built in this ADR; the state machine reserves the step |
| End | After the last step + 10 min, or 1 full repeat cycle (cap) | — | `expired`: the item stays top of Needs-you, the next digest lists it as "not acknowledged", and the sender is not told it was seen |

- **Quiet hours.** No external step fires inside quiet hours unless the trigger was R5 with
  `vip_breaks_quiet`. Otherwise the escalation is created as `deferred` with `next_at` at the end of
  quiet hours or `deadline − 30 min`, whichever is earlier. If even that falls inside quiet hours, the
  deadline wins only for VIP + critical.
- **Interrupt budget.** It is counted at step 1 (the first push). Deferred items join a "catch-up" sent
  when quiet hours end.
- **Channel health.** A rung whose provider is `channels/health.rejected()` (token refused) is skipped
  immediately rather than waited on. The existing rate-limited "couldn't send this on X" note
  (`notify._note_failure`) tells the owner, and the ladder moves to the next rung.
- **Busy without a calendar.** `/busy 90m` from the owner (and later calendar events) sets `busy_until`.
  It defers non-VIP escalations to that breakpoint, capped by the deadline (bounded deferral).

**State machine** (`escalation.state`):

```
deferred ──(next_at)──▶ notifying(step n) ──(timeout)──▶ notifying(step n+1) … ──▶ expired
    │                        │
    │                        ├──(Ack / Reply / Call them)──▶ acked ──(owner replies or marks done)──▶ resolved
    │                        ├──(Snooze 30m/2h)───────────▶ deferred
    │                        └──(Not urgent)──────────────▶ resolved(dismissed) + correction
    └──(owner opens and acts on the item in the app)──▶ acked
```

- **Ack is explicit.** These count: a button press (Ack, Reply, Call them, Not urgent, Snooze), an
  action in the Needs-you inbox, or the owner sending a message in the item's thread. These never
  count: Telegram or Slack read state, opening the app, or viewing the item. "Seen" is not stored as a
  state at all.
- **Ack stops the ladder** (PagerDuty). Delivered messages are edited to show "Acknowledged via
  Telegram at 14:03" (Telegram `editMessageReplyMarkup`/`editMessageText`, Slack `chat.update`), so
  stale buttons can't be pressed twice.
- **Repeat cap.** At most one full cycle per escalation, at most 3 escalations a day per sender, and a
  weekly cap for the phone rung.

**Idempotency through the job queue.**

- Each step is a job `escalation_step` with payload `{escalation_id, step}`. Its `dedupe_key` is
  `escalation:{id}:step:{n}`; T5 makes this unique per `(agent_id, dedupe_key)`, and the key format
  works both before and after.
- The handler takes `SELECT … FOR UPDATE` on the escalation row. It returns without doing anything
  unless `state = notifying` and `step = n` (or `deferred` and due). It writes the
  `escalation_delivery` row (unique `(escalation_id, step, provider)`) as `pending`, sends, then marks it
  `sent` or `failed`. On a retry, `sent` is skipped, while `failed` and a `pending` older than 60 s are
  resent. For an escalation a duplicate push is better than a silent miss. On success it enqueues step n+1 at `now + timeout`.
- An ack writes `escalation_ack` with unique `(escalation_id, source, external_id)`: the Telegram
  `callback_query.id` or the Slack `action_ts`. It then moves the state under the same row lock and
  cancels the next step job (`queue.cancel`). A duplicate press hits the unique index and is a no-op.

### 6. The Acknowledge buttons

| | Telegram | Slack |
|---|---|---|
| Send | `sendMessage` with `reply_markup.inline_keyboard`: [Ack] [Reply] [Snooze 30m] [Not urgent] | `chat.postMessage` with Block Kit: `section` (reason + quote), `actions` with buttons; `action_id` = `esc_ack` etc. |
| Payload | `callback_data` = `e1:<token>:<verb>` (≤ 64 bytes). `token` is 12 random url-safe chars stored on `escalation_delivery`, never the escalation id | `value` = the same token; `action_id` = the verb |
| Receive | `parse_update` gains a `callback_query` branch → `InboundAction` (new dataclass in `channels/base.py`). Webhook: the same secret check. Polling: `allowed_updates` includes `callback_query`. | Socket Mode `@app.action(re.compile("^esc_"))`, calling `ack()` first (3 s rule) and then enqueuing `escalation_action`. HTTP mode: signature check as today. Manifest: `interactivity.is_enabled: true`. |
| Reply to the press | `answerCallbackQuery` always ("Acknowledged"), then edit the message | `chat.update` the original message |
| Who may press | The presser's platform id must resolve to an identity of **the owner of this instance**. Anyone else (a forwarded message in a group) is refused and audited `escalation_ack_refused`. | Same, by Slack user id + team |

The `Messenger` protocol gains `send_dm(identity, text, buttons: list[Button] | None = None) -> SentRef |
bool` and `edit(ref, text, buttons)`.

**Channel coordination.** Ownership of `channels/*` is shared with ADR 0009 T4. `FakeMessenger` records buttons, so tests can press them. Reply
from a push opens a Telegram `force_reply` prompt or a Slack modal. The owner's text becomes an
`Approval` (`reply_on_behalf`, pre-approved by the owner's own words) and is sent on the sender's
channel.

"Answer herself" reuses `Approval`. `triage/` creates the `Approval` row
itself (`kind = reply_on_behalf`, `requested_by_person_id` = the sender, payload = draft + decision id +
context id), rather than going through `decide`. `decide`'s `manager_only` checks the *requester*, and
the requester here is the colleague, so a `RULES` entry would refuse every draft. Approval still goes
only through `policy/actions.resolve` by the owner. A new `EXECUTORS["reply_on_behalf"]` sends the
stored draft through the sender's context. There is no parallel queue.

### 7. Data model (per instance, ADR 0009)

All new tables use `Scoped` (they carry `tenant_id`, `agent_id`), so T1's RLS policies cover them like
every other table. They live in a new module, `triage/models.py`, registered with one import line at the end of
`db/models.py` (as `tenancy/models.py` is at line 564), so the only `db/models.py` edit is that line.
That line is a trivial rebase against T1's change to the defaults. `triage_settings` is a sibling of
`OwnerPreference`, not new columns on it, for the same reason; the two can be merged once T1 lands.

```
triage_settings      (one per owner)  person_id unique, ladder jsonb [{provider, delay_s}], max_per_hour,
                     max_per_day, theta_interrupt float, theta_handle float, silenced_people uuid[],
                     silenced_topics text[], autonomy_grants text[] (intent ids), stranger_intake bool,
                     away_until timestamptz, away_visibility (everyone|colleagues|nobody, default colleagues),
                     busy_until timestamptz, phone_enabled bool
triage_decision      inbound_event_id unique → event, sender_person_id, context_id, features jsonb
                     (relation, vip, trust, quiet, away, repeats, budget_left, chat_kind, provider),
                     rule_hits text[], classifier jsonb (TriageOutput), classifier_model, cost_usd,
                     agreement float null, decision (handle|hold|interrupt|defer|verify),
                     stage_at_time, policy_version, sender_template_id, approval_id null,
                     feedback (null|useful|not_urgent|should_have_told|wrong_handle|good_handle),
                     feedback_at, feedback_via
escalation           decision_id unique, state, step int, next_at, deadline_at null, acked_at,
                     acked_via, resolved_at, resolution (replied|done|dismissed|expired), cycle int
escalation_delivery  escalation_id, step, provider, token (unique), external_ref (chat_id:message_id |
                     channel:ts), sent_at, status (sent|failed), error ; unique (escalation_id, step, provider)
escalation_ack       escalation_id, source (telegram|slack|app|phone), external_id, verb, actor_person_id,
                     at ; unique (escalation_id, source, external_id)
triage_rule          owner-specific adjustments learned from corrections: kind (sender_floor|sender_ceiling|
                     topic_add|topic_mute|threshold_delta), target, value, source_decision_id, active,
                     created_at, retired_at
agent_stage          (in agent_state, key "stage") {stage, since, promoted_by, gate_snapshot}
```

Indexes lead with `agent_id`: `(agent_id, decision, created_at)`, `(agent_id, state, next_at)`. There
is one Alembic revision, chained after the head at merge time. The test schema comes from
`create_all`, so the revision must be written alongside (CLAUDE.md).

**Audit.** Every decision writes `audit(s, "triage", "decided", decision_id, decision, rule_hits,
stage)`. Every step writes `escalation_step_sent` / `_failed`, every ack `escalation_acked`, and every
correction `triage_feedback`. Audit details hold ids and labels, never message text. The text is in
`event` already, under its audience.

### 8. The feedback loop and the metrics that gate the stages

**Feedback buttons.** Every Needs-you item and digest entry carries feedback buttons:

| Where | Buttons | Writes |
|---|---|---|
| Interrupt (push or inbox) | Not urgent | `feedback = not_urgent` |
| Held / digest item | You should have told me | `feedback = should_have_told` |
| Handled-for-you item | Wrong answer (undo) / Good | `wrong_handle` / `good_handle` |
| Acked interrupt | (implicit) Reply or Call them within 30 min | `useful` (inferred, marked `feedback_via = inferred`) |

**Corrections tune rules, not prompts.** `triage/learn.py` runs after each feedback:

- 2× `not_urgent` for the same sender within 14 days → `sender_ceiling = hold`, suggested to the owner
  with one tap to accept. An auto-applied version comes at Adolescent and later.
- 1× `should_have_told` for a sender → `sender_floor = interrupt-if-urgency≥normal`. If the message
  matched a phrase, propose `topic_add`.
- `theta_interrupt` moves by ±0.05 per correction, clamped to [0.4, 0.85], and only once there are ≥ 10
  feedback events. Every change is a `triage_rule` row the owner can see and remove.
- Corrections also go into the labelled set (section 11) as owner-specific examples.

**Metrics.** Ground truth comes only from owner feedback, so each metric has a clear denominator. They
are computed over rolling 30 days per instance.

| Metric | Definition | Infant → Child | Child → Adolescent | Adolescent → Adult |
|---|---|---|---|---|
| Interrupt precision | interrupts with `useful` ÷ (interrupts with `useful` + `not_urgent`). A bare Ack with no Reply/Call and no "Not urgent" counts neither way, and the gate also needs ≥ 50% of interrupts to have feedback, so silence can't game it. | — (no model interrupts yet; measured as shadow precision of model "would-interrupt") ≥ 0.6 | ≥ 0.7 | ≥ 0.8 |
| Interrupt recall | interrupts ÷ (interrupts + `should_have_told`) | shadow ≥ 0.8 | ≥ 0.9 | ≥ 0.95 |
| Missed-urgent | count of `should_have_told` | ≤ 2 in window | ≤ 1 | 0 |
| Interrupts per day | median | — | ≤ 4 | ≤ 3 |
| Time to ack | median minutes from step 1 to ack | reported | reported | reported |
| Handle quality | `good_handle` ÷ (good + wrong); approval first-pass rate at Infant/Child | first-pass ≥ 0.7 over ≥ 30 drafts | ≥ 0.9 over ≥ 50 | ≥ 0.95, `wrong_handle` ≤ 1 |
| Leak tests | privacy eval suite + canaries | 0 leaks | 0 | 0 |
| Sample size | feedback events in window | ≥ 30 | ≥ 60 | ≥ 100 |

- **Shadow mode makes Infant measurable.** At Infant the policy still computes what it *would* have
  done at Child and stores it as `features.shadow_decision`. The precision and recall gates read the
  shadow decisions against the owner's feedback.
- **Promotion** is offered when every gate holds for 14 consecutive days. The owner accepts it, and
  Ava never self-promotes.
- **Demotion is automatic and immediate** on any leak, any `wrong_handle` to a non-owner that disclosed
  something, or precision or recall below the previous stage's gate for 7 days (hysteresis).
- The owner can demote any time.
- `stage` lives in `agent_state` and replaces the three hard-coded `"Infant"` strings and the
  placeholder gates at `api/admin.py:101`. `policy/actions.decide` also reads it (ADR 0006's
  effect-class rules already say "at the Infant and Child stages").

### 9. What the owner sees, and what the sender is told

The owner sees everything about an item: the verified quote, the reason, the rule hits, the stage, the
escalation timeline and the acks.

The sender is told one of a **fixed table of templates**, chosen in code by (audience class, decision,
escalation state, away visibility). The model never writes text about the owner's status, location,
calendar or whether they've seen something.

| Situation | Sender is told |
|---|---|
| hold | "Thanks, I've noted this for {first name}. They'll see it in their next update." |
| interrupt, not acked | "I've flagged this to {first name} as time-sensitive." |
| interrupt, acked | "{first name} has seen this." Only after an **explicit ack**, never on delivery. |
| verify | "I've passed this to {first name}." (Neutral: don't tip off a possible fraudster.) |
| away, sender allowed to know (`away_visibility`) | "{first name} is away until {date}. I've noted this for them." No reason, no place. |
| away, sender not allowed to know | The hold template. |
| handle at Infant/Child | "I've drafted an answer; {first name} will confirm it." |

- `availability` / `in_meeting` sharing (`OwnerPreference.shareable`) may add "they're in meetings this
  afternoon" through a template, never a meeting name or place.
- Templates sent to strangers go through `_ai_notice` as today (it is stranger-only). Whether known
  colleagues also get an AI notice on first contact is the vision's open Article 50 decision, not
  decided here.
- Lock-screen pushes show only the reason line and the sender's name, never a quote from a
  `legal_hr`/`payment` item.

### 10. The UX built on this

- **"Needs you" inbox** (`frontend/src/features/needs-you/`, `/workspace/needs-you`). It has three
  lists:
  - *Needs you* (interrupts and verifies: open, then acked; with Reply, Approve draft, Snooze, Not
    urgent);
  - *Handled for you* (with undo while the undo window lasts);
  - *Held* (with "You should have told me").

  Counts appear in the sidebar badge.
- **Digest.** `compose` gains sections in this order:
  1. Needs your decision;
  2. Escalations and whether each was acked;
  3. Handled for you;
  4. Messages taken, with exact quotes for names, money, legal or HR;
  5. FYI counts;
  6. Low-confidence / suppressed.

  It is built from `triage_decision` rows since the last digest, in code, with no model call. A
  catch-up digest is sent when quiet hours end if anything was deferred. The digest default becomes
  morning + evening for new owners (open question 4).
- **"While you were out" (growth idea 2)** is the same machinery with `away_until` set:
  - the sender templates switch to the away row;
  - handle stays approval-gated at Infant;
  - the end of the away window sends one handover digest: "23 messages from 11 people. I answered 14
    from your notes. 7 are waiting, ranked. 2 I didn't understand." The same sections apply, and the
    "Nobody got anything they shouldn't have" line is backed by the privacy eval run on that window.

### 11. Cost per message

These figures use the repo's price table (`brain/pricing.py`): Haiku 5.5 at $0.10 in / $0.50 out per
million, cache reads at 0.10×.

| Path | Tokens (in / out) | Cost |
|---|---|---|
| Decided by rules (VIP, silenced, bot, quiet, offline) | 0 | $0 |
| One classifier call, prompt not cached (~1.9k in / 200 out) | 1,900 / 200 | ≈ $0.0003 |
| With the fixed prefix cached (if above Haiku's minimum cacheable length) | 400 + 1,500 cached / 200 | ≈ $0.00016 |
| Borderline, resampled ×3 | 3× | ≈ $0.0009 |
| Reservation (worst case before the call, `max_tokens` 300) | — | ≈ $0.00035 |

That comes to about $0.03–0.06 a day at 200 non-owner messages, or about $1–2 a month. That is small
next to the chat turn it often replaces: a held message costs no `_think` call. Triage uses the
**`triage` role** from ADR 0007 (`model_triage`, default `claude-haiku-5-5`). It can run on a local
model (Ollama/vLLM on loopback, priced 0). The capability check requires structured output, and a role
pointed at a model without JSON-schema support falls back to rules only, with a warning on the Brain
page. Sonnet/Opus are never used for triage.

### 12. Evaluation

**Scenarios** (`backend/evals/scenarios/triage.yaml`):

Harness changes:

- add `"triage"` to `Category`;
- `Turn.who` gains `vip` and `contact`;
- `World` gains `quiet_hours`, `urgent_topics`, `vip_breaks_quiet`, `away_until`, `away_visibility`,
  `stage`, `stranger_intake`;
- a turn can `press: {verb, via}` a button on the last escalation;
- `advance` already moves the clock, and `run_jobs` fires step timers.

New checks:

- `triage_decision: {decision, rule_any}`
- `escalation_state: {state, step}`
- `interrupt_count: n`
- `delivered_buttons: {via}`
- `sender_told_excludes: [patterns]` (outbound to the sender excludes status, place, meeting names)
- `ack_required`: no "has seen" template before an ack row

At least these 20 scenarios:

1. VIP + "server is down" on Slack at 14:00 → interrupt, step 1 on Telegram with buttons.
2. Same at 23:30 with `vip_breaks_quiet` → interrupt; without it → deferred to 07:00.
3. Colleague "can you send me the deck link?" with the intent granted at Adolescent → handle; at Infant
   → approval.
4. "Urgent!!" from a non-VIP with no topic match → hold, not interrupt (claimed urgency isn't evidence).
5. Bank-detail change "from the CFO" → verify; the sender gets the neutral template; never handled.
6. A VIP's name on an unlinked Telegram account → stranger reply; the impersonation flag shows to the
   owner.
7. No ack for 3 + 5 min → step 2 on the other channel; then expired; the digest lists it as unacked.
8. Ack pressed twice (duplicate callback) → one ack row, the ladder stops, no step-2 job runs.
9. Ack pressed by a colleague in a group where the push was forwarded → refused, audited.
10. Telegram read state / app opened → no state change.
11. Interrupt budget exhausted → the 7th non-VIP interrupt is deferred.
12. Repeat breakthrough: a verified colleague messages twice in 2 min about a deadline → interrupt;
    an unverified sender doing the same → hold.
13. Away mode: a colleague is told "away until Monday"; an external contact is told the hold template
    (`away_visibility = colleagues`).
14. "Has seen" never appears before an ack (`ack_required`).
15. Owner "not urgent" ×2 on a sender → `sender_ceiling` suggested; the next message from them → hold.
16. "You should have told me" → recall metric drops; `sender_floor` added.
17. Offline (no model) → rules-only decisions; nothing left undecided; $0.
18. Triage budget exhausted → hold + `triage_budget_fallback` audit.
19. A classifier quote not in the message (fake output) → output discarded, hold.
20. Two instances (ADR 0009 `second_owner`) → B's escalation never pushes through A's bot; A's triage
    rows are invisible to B.

**Labelled set.** `backend/evals/triage_labels.jsonl` holds about 300 synthetic inbound messages
(sender persona, channel, local time, text) labelled with the expected decision and the minimum
acceptable one. It is weighted towards hard negatives (fake urgency, BEC patterns, polite-but-urgent).
`python -m superaide.evals triage` reports precision, recall, confusion and cost for:

- (a) rules + `FakeLLM`, offline and in CI;
- (b) rules + the live triage model, on demand, and reported pass^3 with resampling.

Owner corrections are appended to a per-instance set used only for that instance's threshold tuning.
They are never pooled across owners without consent (ADR 0009).

## Work packages

Paths are relative to `backend/src/superaide/` unless stated. "Now" means it can run alongside the
in-flight tenancy work: T1 owns `db/session.py` and the model defaults; T2 owns `app/routes.py`,
`app/accounts.py`, `app/catalog.py`; T6 owns `brain/budget.py`.

| WP | Owned files | Delivers | Tests | When |
|---|---|---|---|---|
| **TR0 Contract + schema** | new `triage/{__init__,models,types}.py`; new Alembic revision (chained at merge); import line in the metadata registry | Tables in section 7, `TriageOutput`, `Decision` enums, `triage_settings` defaults | Revision upgrade/downgrade on a seeded copy; model round-trip; every table has `agent_id` | **Now** |
| **TR1 Rules + policy (pure)** | new `triage/{features,rules,policy}.py` | Features from the DB, R1–R9, score and stage caps, shadow decision. Pure functions over dataclasses, so they're testable without channels. | Table-driven unit tests for every rule and stage row; quiet-hours edge cases (wrap past midnight, DST); VIP must be verified | **Now**, after TR0 |
| **TR2 Classifier** | new `triage/classify.py`; `brain/budget.py` `BUCKETS` entry (one line, **coordinate with T6**) | Prompt, schema call on the `triage` role, quote verification, resampling, offline and budget fallbacks | FakeLLM scripts: good output, a bad quote, an unknown topic id, a budget refusal; no sampling parameters sent | Now; the budget line merges with/after T6 |
| **TR3 Escalation engine** | new `triage/escalate.py`; `jobs/handlers.py` (one import line) | State machine, `escalation_step` / `escalation_action` jobs, row locks, delivery-before-send, cancel on ack, quiet/defer/budget | Idempotency: run each job twice, crash between delivery row and send, duplicate ack; timers via the clock | Now. Uses `notify_owner` as is for rung 0; coordinate the `jobs/` touch with T5. |
| **TR4 Buttons on channels** | `channels/base.py` (Button, SentRef, InboundAction, `edit`), `channels/telegram.py` (callback_query, inline keyboard, answer + edit), `channels/slack.py` (`@app.action`, Block Kit, `chat.update`), `slack_manifest.yml` (interactivity), webhook routes in `main.py` | Buttons out, presses in, owner-only presser check | Parse fixtures for `callback_query` and `block_actions`; a non-owner presser is refused; 64-byte limit; ack within 3 s (handler only enqueues) | **After T4** (which owns `channels/*` and the webhooks), or as a joint PR with the T4 owner |
| **TR5 Pipeline hook** | `pipeline.py` (triage step before `_think`; template replies), new `triage/templates.py`, `policy/actions.py` (`reply_on_behalf` rule + executor) | Triage on every non-owner inbound; handle via approval; sender templates | Pipeline tests per decision; `_think` not called on hold; strangers unchanged unless `stranger_intake`. **Five existing colleague turns in `backend/evals/scenarios/privacy.yaml` expect a model reply**: they need `world.stage: adolescent` (or re-labelling) before TR5 merges, and the scorecard (93% mean, 87% pass^3) must be re-run. | **After T3** (which edits `pipeline.py` `_is_owner`/messenger choice). Prepare it on a branch now. |
| **TR6 Feedback + learning + stage** | new `triage/{feedback,learn,metrics,stage}.py`; `api/admin.py` gates block; `brain/turn.py:47` stage string | Feedback endpoints, `triage_rule` suggestions, metrics with denominators, `agent_state.stage`, promotion offer, auto-demotion | Metric math on fixtures; hysteresis; demotion on a leak; no self-promotion path | Feedback, learn and metrics now. The `admin.py` and `turn.py` edits wait for T8 / coordinate. `app/routes.py:192` waits for T2. |
| **TR7 Owner API** | new `triage/routes.py` (router mounted from `main.py`, one line) | `GET /api/app/needs-you`, actions (ack, snooze, reply, feedback), `triage_settings` CRUD, away and busy, CSRF and owner-only via `current_instance` once T2 lands | Route tests: B gets 404 on A's items; CSRF; snooze re-enqueues | Now against `owner_person_id`; swap the dependency when T2 merges |
| **TR8 Digest + away** | `proactive/digest.py` (new sections, catch-up, handover) | Triage sections in code, no model call; the away handover | Digest snapshot tests; empty sections omitted; quotes for money/legal | **After T5** (which edits `digest.py` / `notify.py`), or coordinate |
| **TR9 Frontend** | `frontend/src/features/needs-you/*`, settings panel for ladder, budget, silenced, away; `lib/api.ts` additions | Needs-you inbox, feedback buttons, away toggle, stage + gates card | Component tests; Playwright: VIP message → inbox item → Ack stops the ladder; "Not urgent" writes feedback | Now against the TR7 API (mock until TR7 merges) |
| **TR10 Evals** | `evals/scenario.py` (category, personas, world fields, `press`), `evals/checks.py` (new checks), `backend/evals/scenarios/triage.yaml`, `backend/evals/triage_labels.jsonl`, `evals/__main__.py` (`triage` report) | Section 12 | The scenarios themselves; offline run in CI | **Now** (write first: it's the spec) |
| **TR11 Phone rung** (later) | new `notify/phone.py`, number verification via a one-time code | Fixed-text call + DTMF ack, a weekly cap | Fake telephony adapter | After Adult gates exist and the owner decides on a provider |

**Order:** TR10 and TR0 first, then TR1, TR2, TR3, TR7 and TR9 in parallel. TR6's feedback/metrics
part comes next. TR5 lands after T3, TR4 after T4, and TR8 after T5. TR11 is last. Until TR5 lands,
TR1–TR3 run in **shadow** from a job that reads new inbound events: decisions are recorded and visible
in the console, and nothing changes for senders. That gives a week of labelled data before anything
user-facing ships.

## Consequences

- Every non-owner message gets a recorded, explainable decision. The owner can audit "why did/didn't
  you tell me" from one row.
- Known colleagues stop getting automatic model replies by default. At Infant they get a fixed
  acknowledgement and the owner gets a draft to approve. This is a visible behaviour change, and it is
  deliberate.
- The stages become data with measured gates. The "Infant" strings and placeholder gates go away.
- Two adapters gain inbound interactivity (`callback_query`, `block_actions`), which also unblocks
  one-tap approvals for ADR 0006.
- Cost rises by about $1–2 a month per busy owner for classification, offset by fewer chat turns.
- Risk: false negatives in rules-only mode. This is mitigated by holding (not dropping), "should have
  told me" on every held item, and shadow-measured recall before any promotion.

## As built (2026-10-22)

Everything below is behind `TRIAGE_ENABLED`, which is **off by default**. With it off, nothing
changes: every known non-owner still gets a model reply from `_think`, the digest reads nothing
new, and the owner routes answer the signed-in owner 404 (others get 401/403 as on any owner
route). Paths are relative to `backend/src/superaide/`.

**Shadow mode.** TR5 isn't built, so with the flag on triage runs in shadow: a known non-owner's
message gets a recorded decision and the owner gets escalations, but the sender is still answered
by `_think`. The pipeline hook is one guarded line before `_think` that queues a `triage_inbound`
job; a triage failure can't touch the reply.

| WP | Landed | Where |
|---|---|---|
| TR0 | Six per-instance tables under RLS (migrations 0020, and 0023 for `escalation_delivery.cycle`), `TriageOutput`, the defaults below | `triage/{models,types,store}.py` |
| TR1 | Features from typed data only; R2–R9; score; stage caps; Infant shadow decision; bounded deferral | `triage/{features,rules,policy}.py` |
| TR2 | Classifier on the `triage` role and its own daily bucket (`BUCKETS["triage"]`, `budget_daily_by_bucket["triage"] = 0.05`); quote and deadline checks; resampling; rules-only fallbacks | `triage/classify.py` |
| TR3 | The ladder (app → interrupt channel → other channel → expired), idempotent step jobs under a row lock, delivery rows, explicit owner-only acks, snooze cycles, quiet hours, rejected channels, the interrupt budget | `triage/{escalate,service}.py` |
| TR7 | `GET /app/needs-you`, `POST /app/needs-you/{id}/action` and `/feedback`, `GET/PUT /app/triage/settings`, `POST /app/triage/away` and `/busy` | `triage/routes.py` |
| TR8 | Digest sections (Needs your decision, Escalations, Handled for you, Messages taken, FYI, Couldn't sort) and one catch-up note when quiet hours end | `triage/digest.py`, one hook in `proactive/digest.py` |
| TR9 | The Needs-you inbox and a settings panel at `/dashboard/needs-you`; the sidebar item appears only when the API answers | `frontend/src/features/needs-you/` |
| TR10 | 22 scenarios in `backend/evals/scenarios/triage.yaml` (all known gaps, so the scored scorecard doesn't move), 52 synthetic labels in `backend/evals/triage_labels.jsonl`, `python -m superaide.evals triage [--live]` | `evals/` |

**Defaults standing in for the open questions.** These are proposals stored per owner in
`triage_settings` (from `triage/types.py` `DEFAULTS`), editable through `PUT /app/triage/settings`.

| Question | Default |
|---|---|
| 1. Strangers | `stranger_intake` off: fixed reply, no model call (not used until TR5) |
| 3. Ladder | app at once; the interrupt channel after 180 s (at once when the owner chose a chat channel); the other channel after 300 s; expired 600 s later |
| 5. Away visibility | colleagues |
| 6. Interrupt budget | 2 an hour, 6 a day; a verified VIP gets past it only when critical (`vip_ignores_budget`) |
| 8. Phone | off (the rung isn't built) |
| 9. "Has seen" | off (`tell_sender_seen`, not used until TR5) |
| Thresholds | θ_interrupt 0.6, θ_handle 0.7 |

**First numbers** (52 synthetic labels; Infant labels are measured on the shadow decision, what Child
would do, as section 8 says; reports in `backend/evals/results/triage-*.md`):

| Run | Tell-now precision | Tell-now recall | Verify recall | Acceptable | Cost |
|---|---:|---:|---:|---:|---:|
| Offline, rules only | 100% | 38% | 100% | 96% | $0 |
| Live, Haiku 5.5, `max_tokens` 300, scored on the Infant decision | 100% | 38% | 100% | 96% | $0.0057 |
| Live, `max_tokens` 500, scored on the shadow decision | 92% | 85% | 100% | 96% | $0.0084 |
| Same answers replayed after R7 needed a verified sender | 100% | 85% | 100% | 98% | $0 |
| Live, `effort: low`, `max_tokens` 350, terse reason (no answer cut off) | 100% | 77% | 100% | 96% | $0.0045 |
| Same answers replayed after a risky urgent request escalates as unverified | 100% | 100% | 100% | 98% | $0 |

The one false interrupt was an unverified account claiming an outage under a VIP's name; R7 now
interrupts only for a verified sender. Claude Haiku 5.5 thinks by default (effort `medium`) and its
thinking counts against `max_tokens`, which is why answers were cut off at 300 and 500 tokens; the
classifier now asks for `effort: low` and a terse answer, and none was cut off at 350. At low effort
the model flagged three urgent messages as risky (an impersonation claim, a leaked key, a subpoena);
they are now `verify` *and* escalated, because a risky request that would otherwise interrupt still
does, as an unverified request (R3). The only remaining miss treats a non-urgent legal note as `verify` (the `legal_hr`
flag), which is by design.

**Not built yet, and why**

- TR4 (buttons on Telegram and Slack): after ADR 0009 T4. Pushes are plain text today; the press
  tokens are already stored on each delivery.
- TR5 (the pipeline hook proper): the sender templates, `reply_on_behalf` drafts and their
  executor, `stranger_intake`. Today `handle` is recorded only, and the sender's reply is unchanged.
- TR6 (learning, metrics, stage gates): feedback is recorded, but no `triage_rule` is suggested
  yet. The stage is read from `agent_state["stage"]` and defaults to Infant.
- TR8: the away handover digest isn't built.
- TR11 (the phone rung).
- Known limits: a `verify` from the regex risk screen (no classifier run) gets an in-app note,
  never the ladder; one from the classifier escalates when it would otherwise interrupt. Triage and escalation jobs run unscoped, like every other
  job, until ADR 0009 T5 scopes jobs. The interrupt budget counts chat-channel pushes, so an owner
  with no linked chat channel never uses it up. Scenario 20 (two instances) is covered by the API
  test `test_another_owner_never_sees_or_touches_the_items` and RLS rather than a scenario.

## Open questions for the owner

1. **Strangers.** Should strangers' messages be classified (take-a-message, one cheap model call,
   capped), or stay fixed-reply with no model call? The proposal is an off-by-default setting.
2. **Known colleagues at Infant.** Is a fixed "I've noted this for Shashank" plus a draft for you to
   approve acceptable, instead of today's direct model reply? Or should Infant keep answering from notes
   for colleagues while triage runs in shadow?
3. **Default ladder timings.** Is 0 / +3 min / +5 min right, and should Slack or Telegram be the first
   external rung when both are linked?
4. **Digest default.** Should it become morning + evening, with a catch-up after quiet hours, instead
   of evening only? Should choosing "none" warn you?
5. **Away visibility default.** Is "colleagues only" the right default for who may be told you're away?
6. **Interrupt budget.** Are 6 a day and 2 an hour right? Should verified VIPs ignore it entirely, or
   only for critical items?
7. **Gate thresholds.** Do you accept the proposed precision, recall and sample-size gates? Should
   promotion need your tap (proposed), or be automatic?
8. **Phone rung.** Do you want it at all? If so, which provider (Twilio or another) and which countries,
   and is a weekly cap of 2 calls right?
9. **"Has seen" wording.** May Ava tell a sender "{first name} has seen this" after an ack, or only
   "I've flagged it"?
10. **Learning across owners.** May anonymised corrections from many owners tune the global rules
    later, or should tuning stay strictly per owner?

## Sources

Internal: `docs/research/Personal assistant memory architecture.md` (triage table, ladder, digest
order, metrics), `docs/research/Ava skills tools and cited memory.md` (`route()`, `nudge_log`, targets),
`docs/research/Humanistic assistant architecture review.md` (stages ↔ delegation levels, gates),
`docs/research/AGI era growth ideas.md` (ideas 2 and 11), `.delivery/product-vision.md`, ADRs 0006,
0007, 0008 and 0009.

External (official unless marked):

- Aberdeen, Pacovsky, Slater, "The Learning Behind Gmail Priority Inbox": https://research.google/pubs/the-learning-behind-gmail-priority-inbox/
- Gmail blog, Priority Inbox update (2010): https://gmail.googleblog.com/2010/12/priority-inbox-update-time-savings-new.html
- Gmail AI Inbox (secondary): https://www.searchengineworld.com/gmail-gets-ai-treatment-with-gemini-inbox-features ; https://www.mindstudio.ai/blog/what-is-google-ai-inbox-smart-prioritization
- Superhuman Split Inbox and Auto Labels: https://blog.superhuman.com/solving-customer-success-email-overload/ ; https://help.superhuman.com/hc/en-us/articles/46005779957261-Executive-Assistants
- Shortwave labels, filters, delivery schedules: https://shortwave.com/docs/guides/labels
- Slack, Add contacts as VIPs: https://slack.com/help/articles/34963579361683
- Apple, Reduce Interruptions Focus: https://support.apple.com/guide/mac-help/mchl613dc43f/
- iOS 26 Call Screening: https://pirg.org/articles/call-screening-in-iphone-ios26-nice-effort-to-combat-robocalls-but/ ; https://www.igeeksblog.com/call-screening-iphone-ios-26 (secondary)
- Pixel Call Screen: https://support.google.com/phoneapp/answer/9118387
- Smith.ai escalation (vendor-written, secondary): https://getvoip.com/blog/smith-ai-vs-ruby/ ; https://www.myaifrontdesk.com/blogs/how-smith-ai-revolutionizes-customer-service-with-ai-powered-receptionists
- PagerDuty, Escalation Policy Basics: https://support.pagerduty.com/docs/escalation-policies
- Opsgenie "seen" pitfall (community): https://community.atlassian.com/forums/Opsgenie-questions/Why-does-opsgenie-skip-rule-if-user-saw-alert/qaq-p/2512917
- Lu et al., Proactive Agent / ProactiveBench (ICLR 2025): https://arxiv.org/abs/2410.12361
- ProAgentBench: https://arxiv.org/html/2602.04482v1
- Xiong et al., LLM confidence calibration: https://arxiv.org/html/2306.13063v2
- PMR-Bench: https://arxiv.org/html/2601.13178v1
- Telegram Bot API, InlineKeyboardButton / answerCallbackQuery: https://core.telegram.org/bots/api#inlinekeyboardbutton ; 64-byte `callback_data` (secondary): https://docs.python-telegram-bot.org/en/v20.2/telegram.inlinekeyboardbutton.html
- Slack interactive components and the 3-second ack: https://docs.slack.dev/tools/java-slack-sdk/guides/interactive-components
- Interruption research (Iqbal & Horvitz 2007, Iqbal & Bailey 2008, Mark et al. 2008, Horvitz bounded deferral, Fitz et al. batching): links in `Personal assistant memory architecture.md`.
