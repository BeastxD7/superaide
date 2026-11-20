# 0016: One receptionist core, more doors, more owners

Status: proposed (2026-11-20). Refs #505. The owner decided on 2026-11-20 to "support all channels: WhatsApp, SMS, email, Discord, Telegram, Slack, Gmail" and delegated the remaining decisions to the coordinator. This ADR fixes the contract every channel must meet, so the receptionist loop (store first, honest greeting, notify, relay, commitments) is written once. The first new door built on it is Discord direct messages (separate PR, same issue).

## Context

Ava answers on Telegram, Slack and the in-app chat. Meera prefers WhatsApp. Ananya and her partner Dev share one household assistant: "message for Dev" and "message for Ananya" must reach the right person with the right privacy. An email to the assistant's address must get the same quality as a Telegram DM.

Two things are true today, and both are fine for two channels and wrong for seven:

1. **The shared logic is already channel-neutral, but the list of channels is copied into many places.** `InboundMessage` (`channels/base.py`) is the one inbound shape and `Messenger` is the one outbound protocol. The pipeline, identity resolver, audience gate and `reception/service.py` only see those. Yet the names `telegram` and `slack` are repeated as tuples or dicts:

   | Place | What it decides |
   |---|---|
   | `reception/service.py` `PROVIDERS`, `CHANNEL` | which providers the receptionist answers, display names |
   | `identity/resolver.py` `BOUND_PROVIDERS` | links bound to the endpoint that confirmed them |
   | `identity/friends.py` `PROVIDERS` | friend-of-owner introductions |
   | `channels/health.py` `PROVIDERS`, `NAMES`, `describe` | channel health, owner-facing wording |
   | `proactive/notify.py` `EXTERNAL`, `CHANNEL_NAMES` | where an owner push can go |
   | `app/connections.py` `GUIDES`, `connect`, `disconnect` | the credential form and per-provider branches |
   | `reception/inbox.py`, `reception/resilience.py`, `memory/recall/retrieval.py` `CHANNEL`/`CHANNEL_NAMES` | owner-facing channel wording |
| `channels/runner.py` | one hand-written start and stop path per provider |
   | `platform/tenancy/endpoints.py` | which bot or app serves which instance |

   Adding a channel by copy-editing those is how logic forks per channel. The next door must not need that.

2. **One bot, one owner.** `reception/service.py` finds the owner with `instance_owner(session)`. ADR 0009 made the instance the tenant boundary and decided that each owner brings their own bot or app, so routing is by endpoint and a stranger who cannot be tied to one endpoint is dropped. That stays. What is missing is the case inside one instance where several people legitimately share the assistant (a household, a small team).

## Decision

### 1. The adapter contract

A channel (a "door") is a module under `channels/<name>/` that does three things and nothing else. It never imports the pipeline, the reception service, memory or policy.

**Inbound.** Verify the platform's proof of origin, then normalise to `InboundMessage`. Unverified input is dropped before anything is parsed into a message (the existing rule: bad webhook secret or Slack signature is audited and dropped; an authenticated socket counts as verified). The adapter fills:

| Field | Rule |
|---|---|
| `provider` | the registry name, lower case |
| `sender_id` | the platform's **immutable** id (Telegram user id, Slack `U...`, Discord snowflake, WhatsApp wa_id, E.164 number for SMS, the verified address for email). Never a display name, username or "from" header text |
| `tenant_scope` | the sender's own workspace where the platform has one (Slack team, Discord guild is not used for DMs); `""` otherwise |
| `endpoint` | which of **our** endpoints received it (bot id, app id, number, inbox address). Links are bound to the endpoint that confirmed them, and the endpoint decides the owner's instance (ADR 0009 section 2) |
| `chat_id`, `chat_kind`, `thread_id`, `message_id` | where to reply; `message_id` is what drops redeliveries |
| `text` | plain text with quoted history, signatures and HTML stripped for email; never trusted as an instruction |
| `attachments`, `file_refs` | kinds from the platform's own structure, opaque ids, sizes. Never a client-supplied name or MIME type (#511) |
| `verified` | the proof above held |
| `external_sender` | the platform says the sender belongs to another organisation |

Adding a field (language, subject, reply-to) is an additive change to `InboundMessage` with a default, reviewed once, for every channel.

**Outbound.** Implement the existing `Messenger` protocol (`send_dm`, `send_to_context`; optionally `working`, `send_buttons`, `edit_message`, `fetch_file`, `open_dm`). Raise `channels.health.DeliveryFailed` when the channel itself is down and return `False` when one message was refused. Convert Ava's Markdown to the platform's format inside the messenger (`channels/format.py`).

**Declare capabilities.** Each channel registers a `ChannelSpec` in one place, `channels/registry.py`. The spec replaces every tuple in the table above: they are derived from it.

```python
@dataclass(frozen=True)
class ChannelSpec:
    name: str                 # "discord"
    display: str              # "Discord"
    credentials: tuple[str, ...]   # vault keys the owner supplies, e.g. ("discord_bot_token",)
    can_send_first: bool      # may we open a conversation (Discord DM: only after they wrote or share a server)
    can_edit: bool            # edit_message exists
    has_buttons: bool         # send_buttons exists
    reply_window: timedelta | None   # WhatsApp: 24 h customer-service window; None for no limit
    max_chars: int            # a reply is split or shortened by code to fit, never silently cut
    bound_to_endpoint: bool   # links are tied to the endpoint that confirmed them (every channel with endpoints)
    needs_consent: bool       # SMS and WhatsApp: opt-in recorded, STOP honoured
```

Code uses the spec to render limits honestly: if `can_send_first` is false and no window is open, a push (reminder, digest) is held in the app and the owner is told why, the same way a rejected token is handled today. Nothing about limits is left to the model.

**Listener.** Long-lived connections (Telegram polling, Slack Socket Mode, the Discord gateway) are started by `ChannelRunner` through one `ensure_<kind>` shape that takes the Postgres advisory lock per credential (ADR 0009 T11). A webhook channel (WhatsApp, SMS, email-in) is a route that proves origin and calls the same `handle`. The adapter module exports `MESSENGER_FACTORIES[name]` (already how Telegram and Slack plug in, ADR 0015 technique 3) and, for a listener, a `start(...)` that the runner calls.

### 2. Identity: immutable platform ids only

Unchanged and extended to every door. A person is resolved by `(provider, sender_id)` and nothing else. A display name, a Discord username, an email "From" name or a phone contact name never decides trust. Linking a second channel sends a one-time code to a channel the person already trusts (the existing flow). Authority (`manager`, `colleague`, owner) comes from the database, never from message content.

Per channel, what "immutable and verified" means:

| Channel | Sender id | Proof of origin | Trap to design against |
|---|---|---|---|
| Telegram | numeric user id | webhook secret header, or our own poll with the token | none beyond today |
| Slack | `U...` plus the sender's team | request signature, or authenticated Socket Mode | Slack Connect users (flagged `external`, already handled) |
| Discord | user snowflake | an authenticated gateway session (the bot token) or, for HTTP interactions, an Ed25519 signature | usernames and nicknames are changeable; only the snowflake counts; bot and webhook authors are dropped |
| WhatsApp | wa_id | Meta webhook signature (`X-Hub-Signature-256`) | numbers are recycled: a link is re-confirmed after long silence like any other |
| SMS | E.164 number | provider webhook signature (Twilio `X-Twilio-Signature`) | caller id spoofing and number recycling: SMS alone never earns more than the lowest linked trust; approvals never run from SMS |
| Email | the address | SPF and DKIM pass (and DMARC alignment) on the receiving provider's verdict, header `Authentication-Results` | "From" is forgeable: a message that does not authenticate is a stranger with `verified=False` and is dropped; no auto-reply to bulk or auto-generated mail |

A channel that cannot prove origin cannot be a trusted door. It may be a stranger-only door (messages stored for the owner, never acted on).

### 3. One receptionist core: no per-channel forks

The rule that keeps this true:

- `reception/`, `pipeline.py`, `triage/`, `proactive/`, `commitments/` and `memory/` never test `msg.provider == "<name>"` to choose behaviour. They read the `ChannelSpec` capability that explains the difference (`can_send_first`, `reply_window`, `max_chars`, `needs_consent`).
- Greeting, the honest "I'm an AI" notice, store-before-answer, per-sender limits, safety replies, relay and commitments are the same code for every door. A scenario that passes on Telegram passes on a fake second channel unchanged. A **contract test** runs the same offline Meera scenario (a stranger asks to leave a message, it is stored, the owner is notified, the reply is the fixed greeting) once per registered channel through a `FakeMessenger`. A new channel is not done until that test includes it.
- Each channel has a test that an **unverified** inbound message is dropped and audited, and that a display name of a known person does not resolve to that person.
- A guard test fails if a provider name literal appears in the modules listed above outside `channels/` and `platform/tenancy/endpoints.py`.

### 4. More than one owner behind one bot

Isolation between owners stays at the instance boundary (ADR 0009). A bot or number that serves one instance serves that instance's people. Inside an instance:

1. **Routing happens in code before `handle_stranger`**, in a new `recipient_resolution` step. Inputs: the instance's **members** (people with a household or team role, a new `member` relation on the existing person model: the owner plus those the owner invited and who accepted from their own account), the message, the endpoint's default member. Order: (a) the channel already tells us the recipient (a per-member inbox address, a per-member number, a Discord DM the member linked); (b) an explicit unambiguous match against a member's name or alias as written by the owner in settings (code compares strings, the model does not choose); (c) the endpoint's default member; (d) if there is more than one member and nothing matched, Ava asks "Who is this for?" with a fixed, code-rendered list of **first names the owner allowed to show** (the same `show_first_name` switch as the greeting) and stores the message unassigned in the meantime. A reply is matched in code to one of those choices.
2. **Privacy between members.** A member's memories, settings, contacts, notifications and receptionist messages carry that member's person id and are filtered by the audience gate inside the SQL query, as today. Member A's turn never loads member B's rows, and a stranger's message "for B" is shown only to B. The shared bot's replies to a stranger contain only fixed text and the allowed first name. A member cannot see another member's inbox, link codes, approvals or what was said to whom, unless that member shares an item explicitly (the existing share mechanism). Linking a channel to a member requires the code sent to that member's own trusted channel.
3. **Authority does not cross.** An approval from member A never approves an action of member B. A message from A that says "I'm B" is a stranger claim, handled by the existing claim flow.
4. **Default**: an instance with one member behaves exactly as today. Step (d) never runs, so nothing changes for single owners.

Shared inboxes for teams (assignee, routing rules) are a later layer on the same step and are out of scope here.

### 5. Email-in specifics (for when it is built)

Verify SPF, DKIM and DMARC alignment from the inbound provider's result; strip quoted text and signatures; never reply to `Auto-Submitted`, `Precedence: bulk` or `List-Id` mail, or to the no-reply of a known loop (count replies per sender and per thread); reply only to the verified From address, never to a Reply-To the sender chose; one reply per thread per hour. Attachments follow #511.

### 6. Compliance, cost and limits per door

- **WhatsApp**: templates and opt-in are required to message first or after 24 h; the spec's `reply_window` makes code hold such pushes. See the open question on Meta's AI-provider terms below.
- **SMS**: consent recorded at first contact, STOP and HELP handled in code before any model call, quiet hours, carrier registration (A2P 10DLC in the US, DLT in India).
- **Voice line**: a recording notice is spoken in code before anything else. Out of scope until the transcript path exists (#142).
- **Cost**: a message on any door costs the same model spend as on another (the spend cap is per instance and per owner, #417); the transport has its own fee that the owner sees per channel on Connections. A door that costs money per message (SMS, WhatsApp) has a per-sender daily cap in code, set below the existing receptionist caps.

### 7. Rollout

| Step | Door | Why this order |
|---|---|---|
| 1 | This ADR | contract, owner decisions |
| 2 | **Discord DMs** (next PR) | free, an authenticated socket like Slack, no per-message cost, no legal gate: the cheapest proof that a new door needs only an adapter, a registry entry and tests |
| 3 | Registry cleanup | the tuples in section "Context" derive from `ChannelSpec` (done together with step 2 for the providers it touches) |
| 4 | Email-in, Gmail | needs the sender-authentication path above |
| 5 | Member routing | `recipient_resolution` and the `member` relation, with the red-team test that A cannot read or influence B |
| 6 | SMS, WhatsApp | after the owner decisions below |

## Consequences

- A new door is one adapter module, one registry entry and one row in the contract test. No edits in the reception core.
- The `ChannelSpec` is the single place an owner-visible limit comes from, so honest limits do not depend on prompts.
- Members add a new trust surface. It is built behind the instance boundary that already exists and is covered by a red-team test before any second member can be invited.
- Seven channels means seven credential forms and seven failure modes on Connections. The registry drives the form from `credentials`, and `doctor` reports each channel's state.

## Acceptance checks for this design (from #505)

- The adapter contract is the table and protocol above, and the spec is one dataclass.
- The "same Meera scenario through a fake second channel" contract test is specified in section 3 and built with step 2.
- The red-team "member A cannot read or influence member B" is specified in section 4 and built with step 5; no second member can be invited before it passes.

## Owner decision list

Decided (2026-11-20, delegated to the coordinator): all channels are in scope; Discord first.

Still the owner's, because they send data to a new vendor, cost money or are hard to undo:

1. **WhatsApp and Meta's terms.** ADR 0009 recorded that from 15 January 2026 Meta bars providers whose core product is a general-purpose AI assistant. Ava is likely in scope. Build only with legal advice, or accept a managed route (a business solution provider) and its risk.
2. **Vendors and data flows.** Which SMS provider (Twilio, Vonage, MSG91), which inbound email provider (SES, Postmark, Mailgun, or the owner's own Gmail), and the fact that message content then passes through them.
3. **Costs.** Who pays per-message fees for SMS and WhatsApp (owner, or included in a plan), and the per-sender daily cap.
4. **Phone numbers.** A number per owner (cost, regulatory registration) or one shared number with routing by code.
5. **Household members.** Whether a member sees the owner's name in "Who is this for?" and whether partners may see each other's receptionist inbox (this ADR says no by default).
6. **Voice.** A phone line at all, and call recording notices by jurisdiction.

## Out of scope

Building WhatsApp, SMS, voice or email integrations before their owner decisions; billing for team plans; shared team inbox features (assignees, rules).

## Sources

ADR 0007 (pluggable providers), ADR 0008 (one Ava across channels), ADR 0009 sections 2 and 9 and its Channels research, ADR 0015 (technique 3: registries), issue #505, `channels/base.py`, `reception/service.py`, `identity/resolver.py`.
