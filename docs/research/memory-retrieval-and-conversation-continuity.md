# Memory retrieval and conversation continuity

Date: 2026-11-20. Code read at `main` commit `cfd4995a`. Status: research and plan; no code changed.

This report answers one owner question: in a Slack chat with Ava on a local model (qwen3:8b through Ollama), Ava forgot the previous turn, did not understand "send", and added a wrong "Correction:" line. What went wrong, and does memory retrieval need a vector database?

Names in examples (Zeeshan, Meera Iyer, Priya Tan, Jordan Whitfield) are fictional.

## 1. Answer in brief

1. **No separate vector database is needed.** Retrieval is already hybrid in Postgres (full-text, trigram and pgvector, fused with reciprocal rank fusion, audience gate in the SQL). At this product's size an exact vector scan for one owner takes single-digit milliseconds (measured, section 6.4). The retrieval problems found are about *what is searched and how the question is understood* (time, person, kind of record), not about the vector store.
2. **The amnesia had one model-side cause (already fixed) and four code-side causes that remain.** The Ollama prompt truncation explains most of the transcript. Independent of it: Slack thread history can be empty or partial, tool results do not survive to the next turn, notes found before the model runs are dropped for every non-Claude provider, and nothing in the code knows a model's context window.
3. **"send" cannot work today.** An approval can only be decided from the app. There is no code path from a chat message to an approval, and no buttons on Slack. This needs a small, code-decided shortcut (section 4).
4. **The "Correction:" line is a regular expression that is too wide.** It fires on any sentence that says Ava cannot message "people", on any channel, including true sentences (section 4.2).
5. **Three of the five "known gaps" in the project notes are already fixed** in the current code; the notes are stale. Two new gaps matter more: the 30-day window that hides visitor messages, and a hard-delete path, both of which conflict with the owner's decision that retention keeps everything.

Section 9 lists what was verified by running code and what is inferred.

## 2. The transcript, turn by turn

| Turn | What Ava did | Cause | Kind |
| --- | --- | --- | --- |
| "hi" | Reply began with reasoning text | Separate fix in progress | Out of scope |
| "anyone asked about me?" | Listed Zeeshan's Telegram message (26 days old, urgent) | The `inbox` tool and the code-built "People waiting" line worked | Correct |
| "yes please" | "I'm here to help! How can I assist you today?" | Prompt truncated by Ollama (known). Also possible: empty thread history (3.2). Even with full history, the inbox id from the previous turn is not in the prompt (3.3) | Model and code |
| Quoted question | "I will now read… Let me fetch the message", no result | The model wrote a plan and stopped without a tool call. The claim check only covers four phrasings (4.3) | Model, with a code gap |
| "tell zeeshan im busy" | Approval card created | `relay_message` worked | Correct |
| "send" | "The command 'send' is not recognized." | Model text (this string is not in the code). No chat path to approve (4.1) | Code gap |
| "yes please send the message now" | "…you can send it yourself. Correction: I can email people for you…" | The model denied the ability; `strip_claims` removed that sentence and appended an email correction to a Telegram relay (4.2) | Code bug |

## 3. Conversation continuity

### 3.1 How prior turns reach the prompt

For the owner's private chat, `pipeline._owner_turn` (`backend/src/superaide/pipeline.py:764`) builds the history with `owner_timeline` (`backend/src/superaide/memory/store.py:195`):

- **This conversation:** `recent_events` returns the latest **12 events** (about six exchanges), with no character or token limit and no summary of what came before (`memory/store.py:162-176`).
- **Other channels:** up to 12 events from the owner's other private DMs in the last 48 hours, each cut to 600 characters, filling what is left of an 8,000-character budget (`memory/store.py:181-184`, `:211-242`).
- `build_messages` (`brain/turn.py:141`) turns events into alternating user and assistant turns, adds gap lines, and prefixes an assistant turn with `[Tools used for this reply: …]`.

### 3.2 Slack threads lose history (code bug, independent of the model)

`recent_events` treats a thread and the main conversation as two separate histories (`memory/store.py:164-168`): with a thread id it returns events whose `thread_id` equals it or whose `external_id` equals it (the thread's first message); without one it returns only events outside threads.

Outbound events are stored without a platform message id: `_reply_inner` calls `log_event(... direction="out" ...)` with no `external_id` (`pipeline.py:1091`), and `SlackMessenger.send_to_context` returns a boolean, not the posted `ts` (`channels/slack/__init__.py:388`).

Three shapes were reproduced with the fake model (section 9, experiment E2):

| Shape | History the model received on "yes please" |
| --- | --- |
| A. Every message at the top level of the DM | Complete (all earlier turns) |
| B. Owner replies in a thread under their own first message | The first message and the thread only. Ava's top-level reply to the first message is missing |
| C. Owner replies in a thread under **Ava's** message | **Nothing.** The only message is "yes please", because the thread root is Ava's message and its `ts` was never stored |

Which shape the incident had is not known from the pasted text; the brief calls it a "Slack thread", so B or C is likely. In shape C, "yes please" arrives with no history at all on any model, and "I'm here to help! How can I assist you today?" is the expected reply.

Related: a first top-level DM message is answered at the top level (`thread_of`, `pipeline.py:89-96`, returns "" for a DM), so a thread the owner opens afterwards always starts split from the turn before it.

**Fix.** Store the Slack `ts` (and Telegram `message_id`) of every outbound post as the event's `external_id`. For a DM, build history as: the thread's events, plus the root event whether inbound or outbound, plus the last few top-level events before the root. A DM is one conversation with one person; threads are a layout, not a wall.

### 3.3 Tool results do not survive the turn (code gap)

The next turn sees only the label and the first 160 characters of the first line of each tool result (`brain/turn.py:81-93`, `brain/loop.py:278-285`). Reproduced (E3): after `inbox` returned

> `1 open conversation, newest first: - id 3649daf7 · Zeeshan (says so; not verified) · Slack · 1 message, waiting … · latest: "please ask him to call me, it is urgent"`

the next prompt contained only

> `[Tools used for this reply: Checking who is waiting for you -> 1 open conversation, newest first:]`

So when the owner answers "yes please" to "Would you like to read what Zeeshan wrote?", the id needed for `inbox(id=…)` is gone. A strong model calls `inbox` again and then again with the id (two extra steps). A small model greets.

**Fix.** Keep a small, code-written record of what a turn referred to, in the outbound event's `meta` (it never contains a stranger's words):

```json
{"referents": [{"kind": "inbox_conversation", "id": "3649daf7", "label": "Zeeshan on Slack"}],
 "offer": {"tool": "inbox", "args": {"id": "3649daf7"}, "effect": "read"}}
```

`build_messages` renders it under the assistant turn, as it does for `[Tools used …]`. Section 4.4 uses `offer` to resolve "yes please".

### 3.4 Notes found before the model runs are dropped for non-Claude providers (code bug)

`OwnerTurn._with_notes` (`brain/loop.py:1089-1105`) attaches pre-recalled notes to the owner's message as `search_result` blocks. `part_from_wire` (`brain/providers/anthropic.py:203-233`) only models `search_result` inside a tool result; a bare block in a user message becomes an empty text part, and `to_chat_messages` (`brain/providers/openai_compat.py:120-126`) keeps only non-empty text.

Reproduced (E8). With a stored note "Jordan's favourite cuisine is Thai food." and the question "what is my favourite cuisine?", the OpenAI-format user message is:

> `(Notes from your notebook that may be relevant, found automatically:)\nwhat is my favourite cuisine?`

The lead line is sent and the notes are not. This affects Ollama, OpenAI, Gemini, OpenRouter and every other OpenAI-compatible provider. It also means `returned_uris` and `recall_hits` say a source was returned that the model never saw, which weakens the source check.

**Fix.** Convert a bare `search_result` block to a `[source id=… title=…]` text block for providers without native citations (the format `citations.render_sources` already produces for tool results, `brain/citations.py:28`).

### 3.5 Nothing knows the model's context window (code gap)

- No provider preset or model record carries a context window (`brain/providers/presets.py:79-83`; the only reference to a window is a stop reason, `brain/loop.py:1133`).
- The request to an OpenAI-compatible server sets only `max_tokens` (`brain/providers/openai_compat.py:314-334`).
- The reply's `usage.prompt_tokens` is read for pricing (`openai_compat.py:142-147`) and never compared with what was sent. Ollama reported the truncation in its own log only.

So the prompt can be cut silently on any local server, and Ava cannot tell.

### 3.6 Why "hi" costs thousands of tokens

Measured on a test database with the qwen3 tokenizer (Ollama `prompt_eval_count`, 16k context so nothing is cut; experiment E1). The test owner has no Gmail, Calendar, web tools or task protocol, and one visitor waiting:

| Part | Characters | Tokens |
| --- | --- | --- |
| System block 0: charter (2,105 chars) + `OWNER_RULES` (5,267 chars, `brain/loop.py:74-92`) | 7,374 | 1,674 |
| System block 1: who, time, channels, `LEARNS`, owner brief, capability block, web rules, "People waiting" line (`loop.py:524-612`, `brain/capabilities.py:145-166`) | 6,048 | 1,436 |
| 11 tool schemas (largest: `propose_action` 407, `create_reminder` 330, `relay_message` 318, `channel_activity` 296, `inbox` 273) | 8,747 | 1,943 |
| The message "hi" and template overhead | | 17 |
| **Total** | | **5,009** |

The production prompt was 7,746 tokens with 12 tools. The difference was not measured (the live database was not read). The parts that exist in production and not in the test are: the task protocol rules and `record_plan` tool (on by default, `platform/config.py:404`), substitute `web_search` and `web_fetch` tools and their rules, the citation rule (`brain/citations.py:18`), Gmail and Calendar lines and tools if connected, pinned facts in the owner brief (up to 1,600 characters, `memory/knowledge/profile.py:51`) and up to 8,000 characters of history.

Two more facts matter for a local model:

- **Speed.** The M5 Pro evaluated the prompt at about 550 tokens per second (5,009 tokens in 9.1 s, measured). A 7,700-token prompt is about 14 s before the first word when nothing is cached, on every step of the tool loop.
- **Cache.** The qwen3 chat template renders tools *after* the system text inside the system message (checked with `/api/show`). System block 1 contains the clock to the minute (`loop.py:548`), so everything after it, including about 1,900 tokens of tools, is re-evaluated whenever the minute changes. Volatile text placed last would keep the prefix cached.

### 3.7 A token budget and tiered prompts

**Budget rule (code, before every model call).**

```
input budget = context window − output reserve (MAX_TOKENS + thinking) − 10% safety
```

- The context window comes from the model record: a table for hosted models, a probe for local ones (Ollama `/api/show` gives `num_ctx` and the model's `context_length`), and an owner override.
- Tokens are estimated from characters with a per-provider ratio (measured here: 4.3 to 4.5 characters per token for English prompt text and JSON schemas on qwen3) and corrected from the last reply's real `prompt_tokens`.
- Parts are filled in priority order, and what does not fit is dropped by rule, with an audit note: (1) core rules, (2) the current message, (3) pending state: approvals waiting, people waiting, the last offer, (4) the tools selected for this turn, (5) the last four turns, (6) retrieved sources, (7) older history as a rolling summary (#416).
- After the call, if the server reports far fewer prompt tokens than were sent, record `prompt_truncated`, show it in `doctor`, and tell the owner once that the model's window is too small.

**Tiers.**

| Tier | For | Fixed prompt target | Tools | History |
| --- | --- | --- | --- | --- |
| Full | Claude and hosted models with 100k+ windows | Today's prompt | All | Today's, plus a summary |
| Compact | Local or small models, 8k to 32k windows | 2,500 tokens or less: rules 1,000, state 300, tools 1,200 | 4 to 6 chosen per turn | 1,500 tokens |
| Minimal | Windows under 8k, or small talk | 600 tokens or less | None | Last 2 turns |

How the compact tier gets small without moving any decision into the model:

- **Rules follow tools.** Each `OWNER_RULES` line is tagged with the tool it is about and included only when that tool is offered. The code already does this by keyword for shared channels (`_PRIVATE_LINES`, `loop.py:116-126`).
- **Tools are chosen by code per turn.** A fixed core (`recall`, `create_reminder`, `list_approvals`) plus tools switched on by state and by words in the owner's message: `inbox` and `relay_message` when someone is waiting or the message names a person who wrote in; Gmail and Calendar tools only when connected and mentioned; `set_presence` on presence words. A tool that was not offered and is asked for is still refused in code, as today.
- **Short descriptions.** A `description_short` per tool in the skill manifest for the compact tier. Today's descriptions carry policy text that the code enforces anyway.
- **Small-talk path.** "hi", "ok", "thanks" with no pending card and no open offer go to the minimal tier: 17 tokens of message do not need 5,000 tokens of prompt.
- **Volatile text last.** The clock, standing approvals and "People waiting" line move to the end of the prompt (or into the last user message) so the prefix stays cached.

Safety is unchanged: identity, audience, trust, taint and approvals are decided in code in every tier. A smaller prompt changes how well the model writes, not what it may do.

## 4. Pending actions: "yes please", "send", "ok"

### 4.1 What exists

- An approval is decided only by `policy.actions.resolve` (`backend/src/superaide/policy/actions.py:202`). Its only callers are the app route (`app/routes.py:1716`) and an eval. There is no chat path, and Slack has no approval buttons (`channels/base.py:64` models button presses for Telegram escalations only).
- The owner's prompt does carry pending cards: "Where things stand … relay_message: Send Zeeshan on Slack: … -> WAITING for the owner's OK" (`brain/capabilities.py:109-142`; seen in the next turn's prompt in experiment E5). The summary is cut to 80 characters, and the block sits at the end of the system prompt.
- The model has no tool that approves, by design: the model must never be able to approve its own card.
- The relay card does not record where the owner asked (`payload.channel` is absent for `relay_message`, E5), so the outcome cannot be sent back to that conversation by origin.

So after "tell zeeshan im busy" the honest answer to "send" is "it is waiting on a card in the app", and the product gives the owner no way to say yes where they are.

### 4.2 Why "Correction:" fired

`OwnerTurn._result` (`brain/loop.py:1413`) calls `capabilities.strip_claims(text, ok_tools, can_send=gmail.can_draft(...))`. With Gmail drafts on, `contradicts_capability` (`brain/capabilities.py:250-253`) matches `_CANT_SEND` (`:243-247`):

```
(can't|cannot|unable to|…) (send|email|message) … (other people|anyone|others|people|emails?)   or   "read-only"
```

Any matching sentence is removed and "Correction: I can email people for you; every send waits for your approval." is appended (`:256-274`). The pattern does not look at the channel or the topic. Reproduced by calling the function (E4):

| Sentence from the model | Result |
| --- | --- |
| "I can't send messages to other people directly. However, I can help you draft the message, and you can send it yourself." | First sentence removed, email correction appended. This is the transcript's reply |
| "I cannot send messages on Telegram for other people without your approval." (true) | Removed and replaced by the email correction |
| "I am unable to message people on Telegram directly." | Removed and replaced |
| "Your Gmail is read-only right now." | Replaced, whatever the real state |
| "I can't send that to Zeeshan on Telegram." | Kept (no "people" word) |

**Fix.** Apply the check only to sentences about email (an email word present, no other channel named), never to a sentence that mentions approval, and, when a relevant approval is pending, replace the denial with the true state built from the record: "Your message to Zeeshan on Telegram is waiting for your OK."

### 4.3 "Let me fetch the message" with no result

`unsupported_claims` (`brain/capabilities.py:212-240`) removes four kinds of false "I did it" sentences (draft saved, sent, reminder set, queued). A promise with no action ("I will now read… let me fetch…") in a reply that ended with no tool call is not checked. A code check is possible and cheap: a final reply whose last sentence promises a lookup, with no tool called in the turn, is retried once with the instruction "call the tool now or say you cannot"; if that fails, the sentence is replaced by a plain statement.

### 4.4 Design: the owner's reply resolves against state, in code

Two cases, both decided before the model runs, where the halt word is checked today (`pipeline.py:246-252`).

**Case 1: a pending approval ("send", "yes send it", "approve", "cancel", "don't send").** A new `approvals/shortcut.py` returns approve, reject or nothing. It acts only when every condition holds:

| Condition | Why |
| --- | --- |
| Sender is the owner, confirmed, by platform id; context is a DM; audience is exactly the owner with nobody unknown | Identity and audience come from the database, never from text |
| The message is short, is only the command (closed word list), has no attachment and no quoted text | A pasted or forwarded "send" is not a command |
| Exactly one approval is pending **and it was created in this conversation**, with the card notice as Ava's latest message there | Binds "send" to the card the owner just saw. Needs `origin_context_id` and `origin_event_id` in the payload |
| The approval is not tainted (`payload.tainted` false), did not start on a call, and its kind is on a short allow-list | Tainted-turn and voice rules stay intact. Start with `relay_message` |
| `required_trust` is met by this channel identity; kinds that need a stepped-up identity stay app-only | Same rule `decide` applies (`policy/actions.py:103-138`) |
| The card is not expired and its seal matches (`approvals/binding.py:77-96`) | A yes to a stale or changed card is not a yes |

Then code calls `resolve(..., via="slack-text")`; the existing burst brake, kill switch and executor checks still run (`policy/actions.py:218-278`). The reply is built by code from the executor's result ("Sent to Zeeshan on Telegram at 14:08: …"), so the model cannot claim a send.

A bare "yes", "ok" or "yes please" counts only when Ava's previous message in this conversation is that card's notice. Two or more pending cards: code lists them with numbers and asks which; it never guesses. A "no" is accepted more freely, because refusing is always safe (the same asymmetry as `_resolve`).

Buttons are the better primary control: Slack Block Kit and Telegram inline buttons carry the approval id in a platform-signed payload, so nothing has to be inferred. The text shortcut is the fallback for when the owner types.

**Case 2: an open offer ("yes please" after "Would you like to read what Zeeshan wrote?").** If the previous outbound event has `meta.offer` (3.3), the offer's `effect` is `read`, and the owner's message is an affirmation from the closed list, the pipeline runs the model turn with a code-written note: `[The owner said yes to your offer: read what Zeeshan wrote (inbox id 3649daf7). Do it now.]`. Offers are only ever read actions; anything with a side effect goes through a card and Case 1. Reading a stranger's words taints that turn as today, so no send can follow in it.

**Case 3: nothing pending.** "ok" is small talk and goes to the minimal tier.

Questions for the owner are in section 8.

## 5. Memory retrieval: audit of the current code

### 5.1 The known gaps, checked

| Gap in the project notes | Status at `cfd4995a` | Evidence |
| --- | --- | --- |
| Retrieval returns the newest 5 or 30 memories whatever the question | **Fixed for the owner's chat. Open for everyone else.** The owner's `recall` fuses an all-words search, an any-word search, trigram and a pgvector arm with RRF (k=60), weighted by importance and decay. A colleague's or other known person's turn still gets the newest 30 or 5 notes | `memory/recall/retrieval.py:86-148`, `:297-321`; `pipeline.py:939`; `memory/audience.py:89-95` |
| Deduplication only catches exact strings | **Partly open.** "Remember that…" compares normalised text only: four phrasings of "I like green tea" left three notes (E7). Facts learned from chat are compared with their nearest neighbours by the extractor. The nightly near-duplicate merge exists behind `SLEEP_JOB_ENABLED`, off by default | `memory/store.py:259-289`; `memory/knowledge/learn.py:176-184`; `memory/consolidation/merge.py`; `memory/consolidation/sleep.py:3-4` |
| Extraction reads only the first 12,000 characters | **Fixed up to 60,000 characters.** Six windows of 12,000 are read; longer text is cut with a notice | `memory/knowledge/facts.py:19`, `:135`, `:154`; `memory/knowledge/extract.py:39`, `:127-153` |
| Removing a source soft-retires its facts instead of deleting them | **Both now exist, and the hard one conflicts with the owner's decision.** `retire_document` hides; `erase_document` deletes the text, passages, facts and vectors | `memory/knowledge/store.py:312-313`; `memory/knowledge/erase.py:1-9`, `:58`, `:65`, `:94` |
| The owner's "never share" text is pasted into prompts for non-owners | **Fixed.** It is added only in the owner-private branch. A canary in `never_share` was absent from a colleague's prompt and present in the owner's (E7) | `memory/knowledge/profile.py:91-105`, `:123-126` |

### 5.2 What `recall` searches

`recall` (`memory/recall/retrieval.py:297`) merges four sources:

| Source | Search | Gate |
| --- | --- | --- |
| Notes (`memory_item`) | Full-text AND, full-text OR, trigram, pgvector | `memory_filter` in SQL (`memory/audience.py:56-80`) |
| Messages (`event`, inbound only) | Full-text AND and OR, trigram. No vector arm | Event audience must contain the whole current audience (`retrieval.py:223-224`) |
| Passages of shared files (`document_chunk`) | Full-text, trigram, pgvector | Owner alone, trust 2 or more (`retrieval.py:169-176`) |
| Visitor messages (`reception_message`) | Any-word full-text, newest first, last 30 days | Owner's private audience only (`retrieval.py:368-413`) |

Before the model runs, `pre_recall` adds up to four notes that match every word of the owner's message (`retrieval.py:345-352`).

Vectors live in `memory_embedding` and `chunk_embedding`, one row per (item, model), in an unsized `vector` column with no vector index (`memory/recall/vectors.py:79-116`). The default embedder is fastembed with `paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions, on CPU (`platform/config.py:286-287`, `brain/embed.py:95-151`); Voyage is the cloud option.

### 5.3 "anyone asked about me?"

This is answered by a **tool and a prompt line, not by retrieval**: the `inbox` tool (`reception/inbox.py:39-56`) reads `reception_message` rows, and `awareness_line` (`reception/inbox.py:135-150`) puts "People waiting for the owner right now: …" into the system prompt, built by code. That is the right design for a state question ("who is waiting") and it worked in the transcript.

`recall` would also have found Zeeshan here, by luck: the question reduces to the words "anyone" and "asked", and the visitor search is any-word, so "ask" in "please ask him to call me" matched (E6).

### 5.4 "what did Meera say last week about the invoice?"

Reproduced with seeded data (E6): Meera, a visitor, wrote about an invoice eight days ago and about coffee yesterday; a known colleague sent a DM about an invoice sign-off; notes mention Meera and the invoice.

| What breaks | Evidence |
| --- | --- |
| **Time is not understood.** "last" and "week" become search words. The `recall` tool takes only `query` and `channel`; there is no date range. Yesterday's coffee message ranked above last week's invoice message | `lexicon.words` output; `skills/builtin/memory/superaide.json:11-14`; E6 |
| **Visitor messages are not ranked by relevance.** They are ordered newest first after an any-word match, and always placed ahead of every other source | `retrieval.py:411-413`, `:317-321` |
| **A person is only a word.** "Meera" matches text in a name, topic or message. There is no filter by sender identity, and nothing joins a visitor's name to a contact | `retrieval.py:406-412` |
| **A known person's messages to Ava cannot be recalled by the owner at all.** The message gate requires the owner to be in the event's audience; a colleague's DM with Ava has audience `[colleague]` only. "invoice sign-off" did not return the colleague's DM | `retrieval.py:223-224`; E6 |
| **Messages and visitor messages have no meaning search.** "bill payment pending" found nothing about the invoice | `retrieval.py:253`, `:406-413`; E6 |
| **Accidental matches taint the turn.** `recall("yes please")` returned Zeeshan's message because of the word "please". Any visitor source taints the turn, and a tainted turn cannot relay | `retrieval.py:411`; `brain/loop.py:698-701`, `:951-958`; E6 |
| **Old visitor messages vanish.** At 31 days Zeeshan's message is gone from `recall` and from `inbox` (rows are hidden, not deleted). The message in the transcript was 26 days old | `retrieval.py:357`, `:379-401`; `reception/inbox.py:29`, `:72-76`; `reception/relay.py:41`; E6 |
| **Sources are merged by fixed seats, not by score.** Notes lead, messages and passages get two seats each | `retrieval.py:318-321` |
| **Episodes are not searched.** The nightly job writes episode summaries; `recall` does not read them | no reference to `Episode` in `memory/recall/` |
| **Ava's own replies are never evidence.** By design; they reach her only as history. So "what did you tell Meera?" depends on the 12-event window | `retrieval.py:241-247` |

### 5.5 Smaller findings

- `LocalEmbedder.embed` ignores `kind` (`brain/embed.py:148-151`). That is correct for MiniLM, and wrong for models that need different query and document prefixes; a future embedder must not reuse it as is.
- `importance.weight` gives every message weight 1 (`memory/consolidation/importance.py:50-60`), so there is no recency prior for messages.
- Unverified senders already get public information only: an unknown sender's audience has `has_unknown=True`, and `memory_filter` then admits only `allowed_kind == "public"` (`memory/audience.py:41-52`, `:72`); the receptionist prompt is built from the public profile. This matches the owner's decision and should be pinned by a red-team test for every new retrieval path.

## 6. External research

Sources were fetched on 2026-11-20. Each fact carries its link and what kind of source it is. Vendor numbers are labelled as such. Nothing here was re-run locally except section 6.4.

### 6.1 pgvector and when a vector database is warranted

- pgvector 0.8.7 is current (the scratch database used here runs it). Search is exact by default, "which provides perfect recall"; an index "trades some recall for speed". HNSW has the better speed-recall trade than IVFFlat and can be built on an empty table. (official: [README](https://github.com/pgvector/pgvector/blob/master/README.md), [changelog](https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md))
- An unsized `vector` column, which Superaide uses, can only be indexed through an expression and a partial index per model: `CREATE INDEX … USING hnsw ((embedding::vector(384)) vector_cosine_ops) WHERE (model = '…')`, and the query must repeat the cast and the predicate. (official README)
- **Filters and approximate indexes.** "With approximate indexes, filtering is applied after the index is scanned." With the default `hnsw.ef_search` of 40, a filter that keeps 10% of rows leaves about 4 results. Since 0.8.0, iterative scans (`hnsw.iterative_scan`) keep scanning until enough rows pass, and the planner may prefer a B-tree: "If you can achieve the same query performance without using an ANN index, this is usually preferable as it lets you achieve 100% recall." (official README; [0.8.0 release note](https://www.postgresql.org/about/news/pgvector-080-released-2952/)) This matters here because every Superaide query is filtered by instance and audience: exact search after the filter is both the simplest and the most accurate choice while it is fast enough.
- **How long exact search stays fast.** pgvector gives no row threshold. EDB's guidance for under one million rows is "Start with no index. Exact sequential scan is fast enough for most workloads" (vendor docs: [EDB](https://www.enterprisedb.com/docs/pg_extensions/pgvector/using/)). A 2023 Neon test measured about 36 ms at 10,000 rows of 960 dimensions and found scans "start to become costly at 50k rows" (vendor blog: [Neon](https://neon.com/blog/optimizing-vector-search-performance-with-pgvector)). Section 6.4 has this machine's numbers at 384 dimensions.
- **Dedicated vector databases.** No independent source with a hard threshold was found. The one benchmark fetched is by a Postgres vendor, who say they are "biased towards Postgres": at 50 million 768-dimension vectors and 99% recall, Postgres with pgvectorscale had p95 60 ms against Qdrant's 37 ms, and higher throughput; their stated reasons to choose Qdrant are horizontal scaling and faster index builds (vendor benchmark: [Tiger Data](https://www.tigerdata.com/blog/pgvector-vs-qdrant)). That scale is three to four orders of magnitude above one owner's memory.
- **Isolation.** Postgres row-level security is default-deny once enabled, but superusers and `BYPASSRLS` roles bypass it, and table owners do unless `FORCE ROW LEVEL SECURITY` is set (official: [Postgres 17](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)). Vector databases isolate tenants with payload filters, namespaces or shards ([Qdrant](https://qdrant.tech/documentation/guides/multiple-partitions/), [Pinecone](https://docs.pinecone.io/guides/index-data/implement-multitenancy), [Weaviate](https://docs.weaviate.io/weaviate/manage-data/multi-tenancy)); none of them can evaluate Superaide's audience rule (allowed people, trust, validity) in the same statement as the relational data it depends on.

### 6.2 Keyword ranking and fusion

- Reciprocal rank fusion comes from Cormack, Clarke and Büttcher (SIGIR 2009): k = 60 was "near-optimal, but … not critical" ([paper](http://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)). Superaide already uses it.
- `ts_rank` and `ts_rank_cd` are not BM25: "the ranking functions do not use any global information", so rare words do not count for more (official: [Postgres 17 text search](https://www.postgresql.org/docs/17/textsearch-controls.html)).
- True BM25 inside Postgres exists as extensions: `pg_textsearch` (PostgreSQL licence, Postgres 17 and 18, needs `shared_preload_libraries`; [repo](https://github.com/timescale/pg_textsearch)), ParadeDB `pg_search` (AGPL-3.0; [repo](https://github.com/paradedb/paradedb)) and VectorChord-bm25 (AGPL-3.0 or Elastic licence; [repo](https://github.com/tensorchord/VectorChord-bm25)). `pg_textsearch` documents that its statistics include rows hidden by row-level security, so term frequencies can leak across owners. For that reason, and because each needs a server extension that the embedded no-Docker Postgres may not have, BM25 extensions are not recommended now.
- BM25 remains "a robust baseline" across domains (BEIR, [paper](https://arxiv.org/abs/2104.08663)), which supports keeping a keyword arm next to the vector arm.

### 6.3 What agent-memory systems and benchmarks actually show

- **Simple baselines are hard to beat.** In Mem0's own paper, putting the whole conversation in the prompt scored highest on LoCoMo (72.9% against 66.9% for Mem0 and 61.0% for the best plain RAG); the memory system's gain is cost: 1.4 s and about 1,800 tokens against 17 s and about 26,000 (vendor paper: [arXiv 2504.19413](https://arxiv.org/html/2504.19413)). Letta reported 74.0% on the same benchmark with an agent that only has file tools, and concluded that how well the agent uses a retrieval tool matters more than the mechanism (vendor blog: [Letta](https://www.letta.com/blog/benchmarking-ai-agent-memory)).
- **The numbers are disputed.** Mem0 reported Zep at 66.0%; Zep reports 75.1% after correcting the setup; Mem0 says Zep's earlier 84% was really 58.4% ([Zep's reply](https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/), [the issue thread](https://github.com/getzep/zep-papers/issues/5)). An outside audit found errors in 6.4% of LoCoMo's answer key ([audit, not peer reviewed](https://dev.to/penfieldlabs/we-audited-locomo-64-of-the-answer-key-is-wrong-and-the-judge-accepts-up-to-63-of-intentionally-33lg)). Conclusion for Superaide: do not choose a design from a leaderboard; build the golden set in 7.5.
- **What is hard:** multi-hop and temporal questions are the lowest categories for every system in Mem0's table; in Zep's LongMemEval results a memory layer helped temporal and multi-session questions and made single-session recall of the assistant's own words worse (94.6% to 80.4%) (vendor paper: [arXiv 2501.13956](https://arxiv.org/html/2501.13956)).
- **What LongMemEval found helps** (ICLR 2025, [arXiv 2410.10813](https://arxiv.org/html/2410.10813)):
  - storing and searching single rounds, not whole sessions;
  - indexing each item with facts extracted from it added to the key: +9.4% recall@k on average;
  - turning the time expression in the question into a range filter: temporal recall +6.8% to +11.3%. A GPT-4o extracted the range; Llama 3.1 8B "performs much worse" at it, which is why section 7 parses time in code.
- **Validity in time.** Zep's Graphiti keeps, for each fact, when it was true and when the system learned it, and marks a superseded fact invalid instead of deleting it (same paper). Superaide's notes already have `valid_from`, `valid_to` and `superseded_by`; messages have `created_at`. A graph database is not needed for this.
- **Recency.** Generative Agents scores memory by recency (exponential decay), importance and relevance ([paper](https://ar5iv.labs.arxiv.org/html/2304.03442)). Superaide applies importance and decay to notes already.
- **Follow-up questions.** Rewriting a context-dependent question into a standalone one before search improves retrieval, especially for keyword search ([arXiv 2310.09716](https://arxiv.org/abs/2310.09716)); HyDE searches with a generated hypothetical answer at the cost of one generation per query ([arXiv 2212.10496](https://arxiv.org/abs/2212.10496)). Both cost a model call, which on a local model is seconds.
- **Context added to chunks.** Anthropic's contextual retrieval cut top-20 retrieval failures from 5.7% to 3.7% with context-prefixed embeddings, to 2.9% adding context-prefixed BM25, and to 1.9% adding a reranker; below about 200,000 tokens they suggest putting the whole knowledge base in the prompt (vendor blog with its own evaluation: [Anthropic](https://www.anthropic.com/news/contextual-retrieval)).
- **Long threads.** Recursive summaries, where the model updates a running summary from the old summary and the new turns, complement retrieval ([arXiv 2308.15022](https://arxiv.org/abs/2308.15022)); this is #416. Models use the start and end of a prompt better than the middle ([arXiv 2307.03172](https://arxiv.org/abs/2307.03172)), and focused prompts beat full ones even on strong models (vendor research: [Chroma](https://www.trychroma.com/research/context-rot)).

### 6.4 Local models: prompt size, tools, embedders, rerankers

- **Tools and small quantised models.** On a function-calling benchmark, Llama 3.1 8B fell from 63.0% at full precision to 39.6% at `q4_K_M`, the quantisation Ollama's qwen3:8b uses. With 46 tools in a 16K window the quantised model failed where it succeeded with 19: "even though [it] has a 16K context window that can fit all the tools, it fails to select the correct one" ([arXiv 2411.15399](https://arxiv.org/html/2411.15399)). A larger study reports drops of 7% to 85% as tool count grows ([arXiv 2505.10570](https://arxiv.org/abs/2505.10570)). This is the evidence for offering a local model 4 to 6 tools, not 12.
- **Ollama's context window.** The OpenAI-compatible endpoint cannot set it: "The OpenAI API does not have a way of setting the context size for a model"; the documented way is a Modelfile with `PARAMETER num_ctx` (official: [OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)). Ollama's own pages give three different defaults (4,096 in the [FAQ](https://docs.ollama.com/faq), 2,048 in the [Modelfile reference](https://docs.ollama.com/modelfile), and tiers by VRAM on the [context-length page](https://docs.ollama.com/context-length)), and recommend at least 64,000 tokens for agents. Truncation is logged by the server and not reported to the client ([source](https://raw.githubusercontent.com/ollama/ollama/main/server/prompt.go), [issue](https://github.com/ollama/ollama/issues/10368)). So Superaide must set and check the window itself (3.5).
- **Embedding models that run in Ollama** (official library pages and model cards; scores are each vendor's own and not comparable across rows):

  | Model | Disk | Dimensions | Languages | Prefixes needed |
  | --- | --- | --- | --- | --- |
  | [nomic-embed-text](https://ollama.com/library/nomic-embed-text) v1.5 | 274 MB | 768 (Matryoshka to 64) | English | `search_query: ` and `search_document: ` |
  | [embeddinggemma](https://ollama.com/library/embeddinggemma) 300m | 622 MB | 768 (to 128) | 100+ | query and document prompts |
  | [qwen3-embedding](https://ollama.com/library/qwen3-embedding) 0.6b | 639 MB | 1024 | 100+ | instruction on the query |
  | [bge-m3](https://ollama.com/library/bge-m3) | 1.2 GB | 1024 | 100+ | none |
  | [snowflake-arctic-embed2](https://ollama.com/library/snowflake-arctic-embed2) | 1.2 GB | 1024 | 74 | `query: ` on the query |
  | [all-minilm](https://ollama.com/library/all-minilm) | 46 MB | 384 | English | none |

  Ollama's `/api/embed` returns normalised vectors and takes a `dimensions` parameter ([docs](https://docs.ollama.com/api/embed)). Four of the six need a query or document prefix, which today's `LocalEmbedder` would not send (5.5). The current default, `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions, 0.22 GB), runs in process through [fastembed](https://qdrant.github.io/fastembed/examples/Supported_Models/).
- **Rerankers.** Ollama has no rerank endpoint ([API reference](https://github.com/ollama/ollama/blob/main/docs/api.md)). Offline options are fastembed's cross-encoders (for example `ms-marco-MiniLM-L-6-v2`, 80 MB, English) or llama.cpp's `/rerank` with `bge-reranker-v2-m3` ([server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)). `jina-reranker-v2-base-multilingual` is non-commercial (CC-BY-NC). No trustworthy latency figure for this hardware was found, so reranking stays an experiment behind the golden set.
- **Evaluation measures.** Recall@k is the share of relevant items found in the top k; MRR averages 1/rank of the first relevant result; nDCG discounts gains by log rank ([definitions](https://en.wikipedia.org/wiki/Evaluation_measures_(information_retrieval))). Choose k "to match actual usage" ([Qdrant](https://qdrant.tech/documentation/search-evaluation/retrieval-relevance/)): `recall` returns 8 sources and `pre_recall` 4, so recall@5 is the headline number.

**Measured on this machine** (Apple M5 Pro, 24 GB; Postgres 17.11 with pgvector 0.8.7 in Docker; 384-dimension random vectors; no vector index):

| Query | Time |
| --- | --- |
| Exact cosine top 12 over one instance's 5,000 rows (of 100,000), B-tree on the instance column | 8 ms |
| Exact cosine top 12 over all 100,000 rows | 58 ms |
| One query embedding, MiniLM through fastembed, in process | 7.4 ms median |

## 7. Recommendation

### 7.1 Vector database

**Stay on Postgres with pgvector. Do not add a vector database.** ADR 0002 already says so. The owner allowed other stores for a graph design on 2026-10-23; this brief states the preference again (Postgres, audience filter in the SQL, owner isolation with row-level security), and nothing found here argues against it:

- The failures in sections 3 to 5 are about history, state, query understanding and coverage. None is caused by the vector store.
- The audience gate, row-level security, validity (`valid_from`, `valid_to`) and deletion are all in the same SQL statement and transaction as the search. A second store would need its own copy of every one of those rules, and a private memory returned by mistake is this product's worst failure.
- Size: one owner has thousands to tens of thousands of rows. An exact scan of 5,000 vectors for one owner took 8 ms and of 100,000 vectors 58 ms on the laptop (6.4). An approximate index is not needed yet.

Revisit when one owner passes about 200,000 embedded rows or p95 retrieval passes 150 ms; the first step then is an HNSW index, not a new database.

### 7.2 Embedding model

- **Keep the in-process fastembed MiniLM as the default for now.** It is multilingual, offline, needs no second model in Ollama's memory, and measured 7 ms per query on the M5 Pro.
- **Make embedders pluggable in two more ways:** an Ollama embedder (`/api/embed`) and a generic OpenAI-compatible embeddings endpoint, beside Voyage. Each embedder declares its dimension, its query and document prefixes, and its similarity floor. The (item, model) table already makes a switch a background re-embed.
- **Choose a better default by measurement, not by leaderboard:** run the golden set (7.5) with MiniLM and with the multilingual Ollama candidates in 6.4 (embeddinggemma, qwen3-embedding 0.6b, bge-m3). Switch only if recall@5 on paraphrase and multilingual queries improves by a clear margin. On a 24 GB machine the chat model already holds about 5 GB plus its context cache; a second model in Ollama competes for that memory, which is a reason to keep embedding in process.

### 7.3 Schema and indexes

Phase 1 (no new table): add filters to the existing arms.

- `recall(query, channel, since, until, person, kinds)`: code parses time words into a range in the owner's zone and resolves a name to identities (contacts, linked people, names visitors gave), before the model runs and again for tool arguments.
- Rank visitor messages with `ts_rank_cd`, then recency; fuse all sources by RRF score with per-kind weights and no fixed seats; drop stop words from any-word searches.
- Indexes: `reception_message` needs a GIN index on its text and a btree on `(agent_id, created_at)`; `event` needs `(agent_id, resolved_person_id, created_at)`.

Phase 2: one derived search index, rebuildable from the source tables.

```
recall_doc(
  id, tenant_id, agent_id,            -- RLS as on every table (ADR 0009)
  kind,                               -- note | message | visitor_message | chunk | episode | approval
  ref_id,                             -- the row it was built from; that row stays the truth
  said_by_person_id, said_by_identity_id, provider, context_id, thread_id,
  occurred_at, valid_from, valid_to, retired_at,   -- soft delete only
  allowed_kind, allowed_person_ids, has_unknown, min_trust,   -- the audience gate, same columns as notes
  taint,                              -- owner | known | stranger | document | web
  text, tsv tsvector GENERATED
)
recall_embedding(doc_id, model, tenant_id, agent_id, embedding vector)
```

- Indexes: GIN on `tsv`; btree on `(agent_id, occurred_at desc)` and `(agent_id, said_by_person_id, occurred_at desc)`; later, one partial HNSW index per model on a cast to that model's dimension.
- One statement: the audience gate and filters first, then the full-text, trigram and vector arms, then RRF. `taint` travels with each row so the loop marks the turn from data, not from a title prefix (`brain/loop.py:698`).
- Messages, visitor messages and episodes get the vector arm. Embedding stays a background job.

### 7.4 Retrieval pipeline

1. **Understand the question in code:** time range, person, channel, kind; strip them from the search words.
2. **Resolve follow-ups from state:** for "what did she say?", add the last referents (3.3) to the query. A model rewrite is an optional later step for hosted models only.
3. **Search:** gate and filters, then the arms, then RRF with per-kind weights; a recency prior only for time-bound kinds.
4. **Rerank (optional, flag off):** a small cross-encoder over the top 30, kept only if the golden set shows a gain worth its latency.
5. **Pack to the token budget (3.7):** best first, each source with its date, sender and taint.
6. **Abstain:** below a score floor, say nothing was found and name what was searched ("nothing from Meera between 9 and 15 November").

State questions ("who is waiting", "what is pending") stay with code-built tools and prompt lines. Retrieval is for content questions.

### 7.5 Evaluation

Extend `backend/evals`, which already has a `recall_top` check (`backend/src/superaide/evals/checks.py:225`) and seeded worlds (`evals/scenario.py:66-132`):

- **`evals/scenarios/retrieval.yaml`: a golden set from this thread.** Seed Zeeshan's and Meera's visitor messages, a colleague's DM, notes and a shared file at set ages. Queries with the expected source: "anyone asked about me?" (state tool, not recall), "what did Meera say last week about the invoice?" (the invoice message first, the coffee message absent), "what did Meera say yesterday", "bill payment pending" (paraphrase), "what did Zeeshan write" at 26 and at 31 days, "invoice sign-off" (the colleague's DM, once the owner decides it is visible), and the same queries as a colleague and as a stranger, which must return nothing private.
- **A retrieval report:** recall@1, recall@5, MRR and nDCG@5 per category (time, person, paraphrase, typo, multilingual, privacy-negative), written next to the existing baselines; CI fails on a drop.
- **`evals/scenarios/continuity.yaml`:** the transcript as scripted turns with `prompt_includes` checks: after the inbox turn, the next prompt contains the id; in a thread under Ava's message, the prompt contains the previous turn; "send" with one clean pending card sends and with two asks which; a tainted card is never approved by text.
- **Prompt budget tests:** for each tier, the built prompt is under its target with the tokenizer ratio; a prompt over the window is trimmed by rule and audited.
- Offline with the fake embedder for plumbing and the gate; a separate run with real embedders for model choice.

### 7.6 Cost and latency on the 24 GB M5 Pro

| Step | Number | Source |
| --- | --- | --- |
| Prompt evaluation, qwen3:8b Q4 | about 550 tokens/s; 5,009 tokens in 9.1 s | Measured |
| Today's owner prompt, cold | about 14 s before the first token at 7,700 tokens, per loop step | Derived from the measurement |
| Compact tier (2,500 tokens fixed) | about 4.5 s cold; less with a cached prefix | Derived |
| Query embedding, MiniLM in process | 7.4 ms median | Measured |
| Bulk embedding, MiniLM | 256 short texts in 1.1 s | Measured |
| Exact vector search, 384 dimensions | 8 ms over one owner's 5,000 rows; 58 ms over 100,000 rows | Measured in a scratch Postgres 17 with pgvector 0.8 |
| Full-text and trigram arms | Not measured at scale here; ADR 0014 has the pagination scale test | Not measured |
| Money | Zero for local embedding and search. The Voyage option is metered and capped already (`brain/embed.py:158-220`) | Code |

The retrieval pipeline is tens of milliseconds. The local model's prompt evaluation is seconds. On this hardware the prompt budget is the latency work that matters.

## 8. Plan and issues

Order: P0/P1 bugs that are model-independent first, then the budget, then approvals, then retrieval.

| Order | Issue | Kind | Priority | What it delivers | Depends on |
| --- | --- | --- | --- | --- | --- |
| 1 | #638 | bug | P1 | Pre-recalled notes reach every OpenAI-compatible model (3.4) | |
| 2 | #639 | bug | P1 | The email "Correction:" only fires on email sentences; a pending card is stated truthfully (4.2) | |
| 3 | #640 | bug | P1 | A Slack DM thread keeps the conversation; outbound message ids are stored (3.2) | |
| 4 | #648 | tests | P1 | Golden-set retrieval metrics and continuity scenarios from this thread; gaps recorded as known until fixed (7.5) | |
| 5 | #641 | feature | P1 | Context window per model, token budget, truncation detection, compact and minimal tiers (3.5 to 3.7) | |
| 6 | #642 | feature | P1 | "send" and buttons approve one bound, clean, pending card in the owner's private chat, in code (4.4) | #640 for origin ids |
| 7 | #644 | feature | P1 | Recall with time, person and kind filters; ranked visitor messages; no accidental taint (5.4) | #648 |
| 8 | #645 | feature | P1 | Retention keeps everything: no 30-day blind spot, erase is soft (5.1, 5.4) | owner decision |
| 9 | #643 | feature | P2 | Referents and offers carried to the next turn; unkept promises retried (3.3, 4.3) | #640 |
| 10 | #646 | feature | P2 | The owner can recall what known people wrote to Ava, as tainted reported speech (5.4) | owner decision |
| 11 | #649 | feature | P2 | Known non-owners get notes chosen for their question, not the newest five (5.1) | |
| 12 | #647 | feature | P2 | One derived search index with meaning search for messages, visitor messages and episodes; pluggable embedders; index policy (7.1 to 7.3) | #644, #648 |
| 13 | #650 | feature | P3 | "Remember that…" merges near-duplicates when said (5.1) | |

Rolling summaries of long threads are already #416 and are not duplicated; #641 leaves the slot for them in the budget. The cross-channel timeline is #512; #644, #646 and #647 are slices of it. The no-hard-delete rule of #415 is extended to sources by #645. #642 and #643 serve the task protocol (#330) and the human-like assistant tracking issue (#488). All thirteen issues are unassigned, labelled `status:triage`, and each carries its own evidence and acceptance tests.

Owner decisions carried into the issues: retention keeps everything, with soft delete only (#645); unverified senders get public information only (pinned by tests in #644, #646, #647 and #649).

Questions for the owner:

1. **Text approvals.** Is "send" in the owner's private DM enough to approve a relay to a person who already wrote in? Recommended: yes for `relay_message`; email sends stay on the card or a button until there is data. Which kinds may ever be approved by text?
2. **Erase versus retention.** "Retention keeps everything (soft delete only)" conflicts with `erase_document`, which deletes for good. Should "erase" become a soft delete with a purge only on an explicit, separate request, or stay as the one exception?
3. **The 30-day window.** With soft delete only, should visitor messages older than 30 days stay readable by the owner (recommended: yes, searchable for ever, and out of the "waiting" list after 30 days)?
4. **Known people's messages to Ava.** Should the owner be able to recall what a colleague or friend wrote to Ava in their own DM with her? Today it is impossible. Recommended: yes, as reported speech, tainted like any other person's words.
5. **Rooms.** The daily job deletes room messages after the channel's retention (`channels/ambient.py:240-247`). Keep (it is a per-channel setting) or soft delete as well?

## 9. Verified and inferred

Experiments ran on 2026-11-20 against a scratch Postgres 17 database created for this work, with the fake model (`FAKE_LLM`), using the repository's own test fixtures. Token counts used the local Ollama model with a 16k window and one output token. The scripts were not committed. The live database was not read.

| Id | Claim | How |
| --- | --- | --- |
| E1 | Prompt size by part; 5,009 tokens for "hi" in the test world; 550 tokens/s | **Ran.** Built the real owner prompt and tool list, counted with Ollama `prompt_eval_count` |
| E1 | Why production was 7,746 tokens | **Inferred** from the code paths that add text; not measured on live data |
| E1 | Tools render after the system text in the qwen3 template | **Ran** (`/api/show`) |
| E2 | Thread shapes A, B, C; 12-event window; outbound events have no external id | **Ran** |
| E2 | Which shape the incident had | **Inferred** |
| E3 | The inbox id is absent from the next prompt | **Ran** |
| E4 | `strip_claims` outputs in 4.2 | **Ran** (called the function) |
| E5 | The pending relay card is in the next prompt; the payload has no channel; "send" reaches the model only | **Ran**, plus reading `resolve` callers |
| E6 | Retrieval results in 5.3 and 5.4; colleague DM audience; 31-day cut-off | **Ran** with seeded rows. Vector results for notes were not exercised (the embed job does not run inside the test) |
| E7 | `never_share` canary; three notes from four phrasings | **Ran** |
| E8 | Pre-recalled notes missing from the OpenAI-format message | **Ran** (`messages_from_wire` then `to_chat_messages`) |
| | Embedding and vector search timings | **Ran** on the M5 Pro |
| | "The command 'send' is not recognized" is model text | **Ran** a search of the source; no such string |
| | The model's behaviour on a correct prompt (would "yes please" work with full history?) | **Not tested.** No model run was made on the transcript |
| | Section 6 | Sources fetched on 2026-11-20 by a research pass; figures were read from summaries of each page and should be spot-checked before they are quoted elsewhere |
| | Designs in sections 3.7, 4.4 and 7 | Proposals; nothing built |
