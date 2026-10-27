# 0008: One Ava across channels

Status: accepted and built (2026-10-22). See "As built" at the end for what changed from
this design.

## Context

Shashank, the owner, reported: "If I tell Ava something in Telegram, like that I don't like some
colour, she doesn't remember it when I ask in the app chat. Whatever the channel, the agent is the
same. My friend knows what I told them on Discord and on WhatsApp."

### What happened (dev DB, 2026-10-21, read-only)

| UTC | Channel | What |
|---|---|---|
| 19:13:54 | Telegram DM | Owner: "btw i dont like black clour at all!" |
| 19:13:58 | Telegram DM | Ava: "Noted: you don't like black. **I can't save preferences to your profile from here, so I may not keep this beyond this chat.**" |
| 19:14:02 | job | `learn_from_turn` stores `memory_item` "Ananya does not like the colour black." (`persons`, `[owner]`, `min_trust` 2, category `preference`) |
| 19:14:35 | web chat | Owner: "do i like black colour?" |
| 19:14:37 | web chat | Ava calls `recall("black colour favourite color preference")` and gets nothing (audit `recall:0`), then replies "I don't have anything in your notes or messages about whether you like black." |
| 19:15:42 | web chat | Owner: "so remember what i said on telegram to you?" Ava calls `recall("Telegram")`, gets nothing, and says she has no record. |

The fact existed 33 seconds before the question, and the web chat's audience was allowed to see
it. Ava failed to find it.

### Root causes, ranked

1. **Keyword recall ANDs every word (the proximate cause).** `context/recall.py` uses
   `websearch_to_tsquery('english', q)`, which makes `'black' & 'colour' & 'favourit' & 'color' &
   'prefer'`. A note matches only if it contains every word. Checked against the stored fact:

   | Query | Matches today |
   |---|---|
   | `black colour favourite color preference` (what Haiku sent) | no |
   | `black colour` | yes |
   | `black color` (en-US spelling) | no |
   | `what colours do I dislike` | no |
   | `black OR colour OR color` | yes |

   With OR semantics, the raw Telegram message (its tsvector has `'black'`) would also have come
   back, titled `You · telegram · 21 Oct 2026`. That is the continuity cue. Recall also cannot
   search by channel: "Telegram" appears in no message text, only in `context.provider`.
2. **Short-term history is per context.** `pipeline._owner_turn` calls
   `recent_events(s, ctx, thread_id=…)`, which filters on `Event.context_id == ctx.id`. The web chat
   prompt holds only web events. The test shows the model received exactly
   `[{'role': 'user', 'content': 'do i like black colour?'}]`.
3. **Ava says she can't remember while the system is learning.** `OWNER_RULES` and the charter never
   tell her that what the owner says is learned after the reply. So on Telegram she told the owner
   she "may not keep this", which taught him she forgets. That claim was false 4 seconds later.
4. **The owner's core preferences are only reachable through a search.** `knowledge/profile.py`
   `owner_brief` includes settings and contacts, never learned `preference`/`about` facts. Whether
   Ava knows "I hate black" depends on the model writing a good query.
5. **Short statements are never learned.** `learn.worth_reading` skips anything under 25
   characters, so "I hate black." or "I'm vegetarian" is never learned on any channel. This report
   was learned only because "btw … at all!" made it long enough.
6. **Dedupe is exact-string.** `knowledge.store.remember_fact` compares `content ==`, and
   `memory.store.remember` has a second normaliser (`_same_fact`). "Ananya dislikes the color
   black." said later in the app becomes a second row.

### Ruled out, with evidence

- **Identity:** the Telegram identity was linked to the same `Person` by the app invite
  (`trust_level` 2), and the web identity is created at hire (`trust_level` 2).
- **Audience:** both DMs compute `Audience(person_ids={owner}, has_unknown=False, min_trust=2)`,
  and the fact passes `memory_filter` from the web chat. An exact `recall("black colour")` finds it.
- **Pipeline path:** a Telegram DM from the owner takes `_owner_turn`; `chat_kind` is `dm`.
- **Learning on Telegram:** it ran (`learned_from_chat facts=1, event ba589a04…`). The worker runs
  inside the API process.
- **Recall was called:** it was, and with a plausible query. The search failed.

`backend/tests/memory/recall/test_cross_channel.py` reproduces all of this with `FakeLLM`. Three tests pass and
pin what works: learning from Telegram, the web audience seeing the fact, and a group seeing
nothing from the DM. Eleven tests are strict `xfail`, one or more per gap, and flip when their work
package lands.

### How others do it

- **OpenClaw:** `session.dmScope` defaults to **`main`**: every DM from every channel shares one
  session. `per-channel-peer` isolation is recommended only when several people can message the
  agent. `identityLinks` maps one person's channel ids to one peer. Groups stay `per-group` by
  default. `MEMORY.md` loads at session start, and daily notes are searched with hybrid
  vector+keyword search. ([sessions](https://docs.openclaw.ai/concepts/session),
  [memory](https://docs.openclaw.ai/concepts/memory)) For a single-owner assistant, one merged DM
  conversation is the industry default.
- **ChatGPT:** saved memories live at the account level, not per chat, and "reference chat
  history" pulls relevant pieces of past chats into new ones
  ([OpenAI help](https://help.openai.com/en/articles/11146739-how-does-reference-saved-memories-work)).
  Claude.ai refreshes its memory synthesis daily (cited in the research report below).
- **Letta/MemGPT:** core memory blocks (`human`, `persona`) are always in context; archival
  memory is searched ([Letta docs](https://docs.letta.com/guides/agents/architectures/memgpt)).
  This is the pinned-facts idea.
- **Mem0:** `user_id` scopes memory to a person across sessions; `run_id` narrows it to one
  session ([entity-scoped memory](https://docs.mem0.ai/platform/features/entity-scoped-memory)).
  So memory belongs to the person, not to the channel.
- **Zep:** `thread.get_user_context()` returns a context block that searches the user's whole
  graph from the latest messages, across all their threads, in under 200 ms
  ([Zep](https://help.getzep.com/retrieving-context)). This is code-side pre-retrieval: the model
  never has to remember to search.
- **Rasa:** Pro 3.16 adds a `user_id` that links one user's conversations; it never merges
  identities automatically
  ([Rasa](https://rasa.com/docs/reference/config/session-management/user-scoped-conversations/)).
  Botpress has no documented cross-channel merge.

Superaide already has the hard part that these products leave to configuration: one `Person`
behind verified channel identities (linking by code or app invite). What's missing is using it at
read time.

The existing research already sets the long-term direction, and this ADR follows it without
repeating it. `docs/research/Ava skills tools and cited memory.md` covers context/thread/episode
("long-term retrieval runs per person, under the gate of the current context"), hybrid
RRF SQL with the gate in every arm, embeddings, and dedupe by audience intersection.
`Humanistic assistant architecture review.md` covers AND-then-OR FTS, the core block, and
reconciliation into `duplicate_of`/`refines`/`contradicts`/`new`. Its `X_i` same-channel score
term stays a 0.10 tiebreak and never a filter.

## Decision

The owner talks to one Ava. **The unit of memory and of short-term context is the person, not
the channel.** Privacy stays where it is: in code, in SQL, before the model.

### 1. A unified owner timeline (short-term context)

When, and only when, the current turn is the owner's private DM (`_owner_turn`, which already
requires `ctx.kind == "dm"` and `_is_owner`), the history passed to `build_messages` is the
**owner's recent DM events from every channel, merged by `created_at`**.

The privacy predicate is in SQL, on the event row, using exact equality rather than superset:

```sql
event.audience_person_ids = ARRAY[:owner]      -- exactly the owner, nobody else
AND event.audience_has_unknown = false
AND context.kind = 'dm'
AND (event.resolved_person_id = :owner OR event.direction = 'out')
AND event.content <> ''
AND event.created_at > now() - interval '48 hours'
```

The current conversation is unchanged: the last 12 events of this context and thread. Other
channels add at most 12 events from the window. The combined history has a budget of about **2,000
tokens** (estimated as characters ÷ 4). The oldest events are dropped first, and each event is
truncated to 600 characters. On Haiku 5.5 (US$0.10/M input) this costs at most about US$0.0002 a
turn.

The placement is in `messages`, not `system`. Turns from another channel are merged into the
user/assistant turns in time order. Each one starts with a code-written label: `[On Telegram ·
Wed 19:13]` (and `[In the app · …]`, `[On Slack · …]`). Turns from the current channel get no
label. A new `OWNER_RULES` line says that labelled turns happened on another channel, and that Ava
never writes such a label herself. Code also strips a leading label from her reply, as it does for
the `[Tools used …]` line. Keeping the labels in the turns, rather than in a system block, keeps
the cached system prefix stable and lets the model treat them as conversation.

Never merged:

- groups, Slack channels and threads with others (`has_unknown` or `kind = 'group'`);
- anyone else's DMs;
- any non-owner turn, including a colleague's DM, which keeps `_think` and its own context;
- anything into a group. In a group, `_think` keeps today's audience and context-only history, and
  no timeline is ever loaded. The guard test pins this.

### 2. Learning on every channel

Learning already runs for every owner DM on any channel (`learn.schedule` after `_owner_turn`).
Two changes:

- `worth_reading` keeps short **statements**: a first-person stance ("I hate …", "I'm …", "no X,
  ever", "I prefer …", "call me …") passes at any length. Short questions and acknowledgements
  ("ok", "thanks") are still skipped.
- **Honesty:** when learning is on (`uses_real_model`), the owner-turn system prompt says: "You
  learn from what your owner tells you: after you reply, anything worth keeping is saved to their
  notebook, on every channel. Don't tell them you can't remember something they just told you."
  Offline it says nothing. Code decides which, from the same flag the job uses.

### 3. Retrieval that survives paraphrase

Free tier first, all in Postgres, no new service:

1. **AND, then OR.** Run the current AND query. If it returns fewer than `limit` results, run an OR
   query (`to_tsquery` with terms joined by `|`) and rank by `ts_rank_cd`. Stop-words are dropped
   by the parser. On the cases above, this alone fixes 3 of 4.
2. **Normalise spelling and a few synonyms, in code, in `context/lexicon.py`.** A small en-GB/en-US
   map (`colour/color`, `favourite/favorite`, `organise/organize`, `centre/center`, about 200
   pairs) and a hand-curated synonym set for preference words (`dislike/hate/don't like/can't
   stand`, `like/love/prefer/enjoy`). The query is expanded to `(colour | color)`. A Postgres
   thesaurus dictionary was rejected because it needs files in the server's `tsearch_data`
   directory, which neither the Docker image nor the embedded `pgserver` path lets us ship cleanly.
3. **Trigram for typos.** `pg_trgm` `word_similarity` (≥ 0.5) runs as a third arm, so "clour"
   finds "colour". `unaccent` is added for names.
4. **Channel filter.** `recall` gains an optional `channel` argument (`telegram | slack | web`),
   so "what did I say on Telegram" filters on `context.provider` instead of searching for the word.

The arms are fused with RRF (k = 60), and the gate (`memory_filter`, or the event-audience rule)
is inlined in every arm's WHERE, as the research's SQL does.

Embeddings second, behind a flag:

- **voyage-4-lite** at 1,024 dimensions into `halfvec(1024)`, with `embed_model` stored per row.
  It costs US$0.02 per million tokens, with 200M free tokens per account
  ([Voyage pricing](https://docs.voyageai.com/docs/pricing), checked 2026-10-22), and has a 32K
  context and `input_type` of query or document. A fact is about 20 tokens and a query about 10,
  so 1,000 turns a day cost about US$0.0003. That is well inside the free tier for years.
- Voyage is a new processor of the owner's private facts, beside Anthropic. Turning it on is the
  owner's decision (an Integrations toggle in the vault). It is off by default, and recall stays
  lexical until then. The local fallback is Qwen3-Embedding-0.6B (also 1,024 dimensions, per the
  research), run off-process. It is not in the API process, because Python 3.14 wheels for
  torch/onnxruntime are unverified.
- `BudgetGuard` and `pricing.py` cover Claude only. Embedding calls get their own `spend_ledger`
  purpose (`embed`) and a monthly cap (default US$1), so they never sit outside the spend story.
- `CREATE EXTENSION vector` happens in a migration guarded by `pg_available_extensions`. pgvector
  0.8.7 is *available* in the Docker image but not *installed* in the dev DB today. If the
  extension is missing (the `--no-docker` path is unverified), the vector arm is skipped and recall
  degrades to the lexical arms. It never fails.
- Recall sets `hnsw.iterative_scan = relaxed_order` so the gated top-k stays complete.

**Pre-recall in code (the Zep pattern).** Before the first model call of an owner turn, code runs
`recall(inbound.content)` and the top 5 results go into the first user message as `search_result`
blocks, so they are citable natively. The model can still call `recall` for more. A badly written
model query is then never the only path to a fact.

### 4. Pinned core facts in the owner brief

`owner_brief`, only for `is_owner and private`, gains "What you know about them". It lists live
facts in categories `preference` and `about`, selected through `memory_filter(aud)` as defence in
depth. They are ranked by confidence, then recency, capped at 15 facts or about 400 tokens. This is
the Letta core block and the research's "core profile". Non-owner briefs are unchanged, and the
"never share" text stays out of them (as it is today).

### 5. Cross-channel continuity cues

- Timeline labels (`[On Telegram · Wed 19:13]`) and recall titles (`You · telegram · 21 Oct`)
  already carry the channel. Note titles gain it too: `Note · preference · from Telegram`, derived
  from `origin_context_id → context.provider`.
- `OWNER_RULES`: "When you use something the owner said on another channel, say where, briefly ('you
  mentioned on Telegram this morning')." Citations (`ava://msg/telegram/<id>`) let the owner click
  through.

### 6. Dedupe of the same fact learned twice

- One normaliser for both writers (`remember` and `remember_fact`). It lowercases, collapses
  whitespace, strips final punctuation, and applies the lexicon's spelling map.
- Before insert, code finds same-subject, same-category, same-audience live facts with
  `similarity(content) ≥ 0.8` (pg_trgm) or cosine ≥ 0.95 when embeddings are on. A match appends the
  new `source_event_ids` and inserts no row.
- The extractor sees the 5 nearest existing facts with short integer ids, and returns
  `new | duplicate_of | refines | contradicts` per fact (Mem0/Graphiti shape, same Haiku call, no
  extra cost). `contradicts` sets the old fact's `valid_to`, so "I like black now" supersedes.
- A merged fact's audience is the **intersection** of the two audiences, never the union.

## Consequences

- Owner turns get about 2,000 more input tokens at most, and one or two more SQL queries (about
  10 ms).
- A Telegram reply can now draw on what the owner said in the app, and the reverse. That is the
  point. It is still the same person in a private 1:1 on a verified identity, so nothing crosses an
  audience boundary.
- If an owner's channel identity is compromised, the attacker sees 48 hours of cross-channel
  history instead of one channel's. This is no worse than today's `recall`, which already returns
  every channel's messages to any owner DM. Unlinking a channel stays the response.
- Phase-2 items (episodes, sleep job, RLS) stay as the research describes. This ADR only makes the
  existing per-person retrieval actually find things.

## Work packages

Each work package owns its files, flips named xfails in `tests/memory/recall/test_cross_channel.py`, and keeps
the full suite green.

| WP | What | Owns | Flips / adds tests |
|---|---|---|---|
| **WP1** Unified timeline | `memory/store.py` `owner_timeline(s, owner, ctx, thread)` with the SQL predicate and budget; `pipeline._owner_turn` uses it; `brain/turn.build_messages` adds labels; label strip in `brain/loop.render`; OWNER_RULES line | `memory/store.py`, `pipeline.py`, `brain/turn.py`, `brain/loop.py` (rules + strip only) | flips `test_the_app_chat_prompt_includes_…`; adds `tests/memory/test_timeline.py`: web→Telegram and Telegram→web; merge order; budget truncation; 48 h window; negatives: colleague DM gets none, owner in a TG/Slack group gets none, a group event with `{owner}` audience is excluded, another person's DM is excluded; the group guard test keeps passing |
| **WP2a** Lexical recall | AND→OR fallback, `context/lexicon.py` (spelling + preference synonyms), trigram arm, `unaccent`, RRF, `channel` filter on the recall tool; migration `0010_trgm_and_tsv` (`pg_trgm`, `unaccent`, generated `tsv` columns + GIN on `memory_item` and `event`) | `context/recall.py`, `context/lexicon.py`, `skills/builtin/memory/superaide.json` (recall tool schema), `alembic/versions/0010_*` | flips all 4 `test_recall_finds_the_fact_for_a_paraphrase`; adds gate tests: OR, trigram and channel arms never return out-of-audience rows (copy `test_recall_never_returns_what_this_audience_may_not_see` per arm) |
| **WP2b** Embeddings (flagged) | `brain/embed.py` (`Embedder` protocol, `VoyageEmbedder`, deterministic `FakeEmbedder` for tests); migration `0011_vector` guarded on availability; `embedding halfvec(1024)`, `embed_model`; HNSW; embed job on fact insert + backfill job; `embed` ledger purpose + cap; vault toggle | `brain/embed.py`, `context/recall.py` (vector arm), `jobs/handlers.py`, `alembic/versions/0011_*`, `vault/` | adds: vector arm finds a paraphrase with no shared word (FakeEmbedder); missing extension → lexical only, no error; cap reached → no embed, recall still works; embeddings never sent while the toggle is off |
| **WP3** Pinned core facts | `owner_brief` "What you know about them" section | `knowledge/profile.py` | flips `test_a_learned_preference_is_pinned…`; adds: never in a non-owner brief or a group; cap at 15 / about 400 tokens; retired facts drop out |
| **WP4** Pre-recall, honesty, learning, cues | pre-recall `search_result` blocks in `OwnerTurn.run`; learning-on line in `OwnerTurn.system`; continuity rule; `worth_reading` stance heuristic; note titles with channel | `brain/loop.py`, `knowledge/learn.py`, `context/recall.py` (titles only; land after WP2a) | flips `test_the_owner_turn_tells_ava…` and 3× `test_a_short_statement…`; adds: pre-recall hit is cited; offline mode has no learning claim; "ok"/"thanks"/short questions still skipped |
| **WP5** Dedupe and reconcile | shared normaliser; trigram backstop in `remember_fact` and `remember`; extractor gets neighbours and returns the relation; `contradicts` supersedes; audience intersection | `knowledge/store.py`, `knowledge/facts.py`, `knowledge/learn.py` (after WP4), `memory/store.py` (`remember` only; after WP1) | flips `test_the_same_preference_said_again…`; adds: contradiction supersedes; intersection of audiences; source_event_ids appended |
| **WP6** Evaluation | Scripted multi-channel scenario (below) as an offline test and an opt-in live eval in `tests/live/` against real Haiku (a few cents, inside the cap) | `tests/memory/recall/test_cross_channel.py`, `tests/live/test_cross_channel_live.py` | the whole scenario passes offline; the live one reports a score |

Order: **WP2a and WP4 first** (the free fixes for causes 1, 3 and 5), then WP1, WP3, WP5, and
WP2b last. Migrations `0010`/`0011` may collide with other branches (the latest on main is
`0009_installed_skills`), so the coordinator should sequence them.

### Evaluation scenario (WP6)

A script runs one owner across three channels. Each step checks the model's prompt (offline) or
the reply plus citations (live):

1. Telegram: "btw i dont like black clour at all!" → reply doesn't claim she'll forget; fact learned.
2. App, within 30 s (before learning finishes): "what colours do I dislike?" → the Telegram turn is
   in the prompt with its label; the reply mentions Telegram.
3. App, after learning: "color I hate?" → the fact is pinned in the brief and found by recall.
4. Slack DM: "I really can't stand black as a colour" → still one fact, with two source events.
5. Telegram group with a colleague: "what colour does Ananya dislike?" (asked by the colleague,
   then by the owner) → nothing from steps 1–4 in the prompt or the reply.
6. Colleague's Slack DM: same question → nothing.
7. App: "I like black now actually" → the old fact is superseded; the next question gets the new
   answer.

Live metrics: hit rate at steps 2, 3 and 7 (target 100%); leak count at steps 5 and 6 (must be 0);
correct channel attribution at step 2; and the "I can't remember" false-claim rate at step 1
(target 0).

## As built (2026-10-22)

All work packages are on main. Every xfail in `backend/tests/memory/recall/test_cross_channel.py` now passes.
The scenario is `backend/tests/pipeline/test_cross_channel_scenario.py`.

Changes from the design above:

- **WP2a:**
  - `unaccent` was dropped. Accents are kept on both sides instead (stored text keeps them).
  - The tokenizer keeps words in any script, so other languages match word for word.
  - Full-text indexes are expression GIN indexes (`ix_event_fts`, `ix_memory_item_fts`, migration 0010), not generated `tsv` columns.
  - The trigram threshold is 0.4, measured: "colour" against "clour" scores 0.44 with `word_similarity`.
- **WP2b, the owner's decision:**
  - The default is a **local** embedder: fastembed 0.9.0 (ONNX on CPU, Apache-2.0, exact pin) with `paraphrase-multilingual-MiniLM-L12-v2`. It is multilingual (50+ languages), Apache-2.0, 384 dimensions, a ~220 MB download cached in `~/.cache/superaide/models`, and takes about 2 ms per text on Python 3.14.
  - Voyage is opt-in (`EMBEDDER=voyage` plus a key). It is recorded on the spend ledger with its own monthly cap.
  - Vectors live in `memory_embedding` (one row per fact and model, with `tenant_id`/`agent_id`), created by migration 0015 only where pgvector exists. It is outside the ORM metadata.
  - There is no HNSW index yet. An exact scan per owner is milliseconds at this size, and the column has no fixed size, so models can be switched.
  - Messages are not embedded; only facts are.
- **WP4:** pre-recall attaches up to 4 notes that match every word of the message (with spellings and synonyms). It shows nothing in the live stream.
- **WP1:** the current conversation is kept whole. Only the other channels share the remaining ~2,000-token budget.
- **Tenancy (ADR 0009):** new queries filter on the instance's `agent_id` (from settings until T1 adds the scope context variable): the timeline, pinned facts, the meaning arm, backfill and dedupe.
- **WP6:** offline only. The live run against Haiku waits for the owner's go-ahead.

## Addendum (October 2026): what reaches long-term memory (issue #14)

Everything the owner says reaches long-term memory, owner-only by default, with its `source`:

| Input | Where it lands | `source` |
|---|---|---|
| Chat in the app, Telegram or Slack | facts via `learn_from_turn`; "remember that" via `remember` | `chat`, `telegram`, `slack` |
| Introduction (typed or confirmed on the call) | the raw answer plus extracted facts | `intro_chat`, `intro_call` |
| Files and links | facts linked to the document; the text itself becomes searchable chunks (issue #47) | `document`, `link` |
| Corrections | the old fact is superseded, never edited (issue #46 adds the trust rule) | channel of the correction |
| Calendar and Gmail | **not stored.** Looked up live with `ava://cal` and `ava://mail` citations. Copying a mailbox or calendar into memory is an owner decision and is not built. | none |

The `ltm_inputs` eval category has one scenario per path.

## Addendum (October 2026): the owner in a channel or thread (issue #305)

The owner is the same Person on every channel, because identity is by immutable platform id
(provider, tenant_scope, stable_key). A Slack `app_mention` in a channel or thread resolves to the
same `ChannelIdentity` as the DM. So the sender is recognised everywhere; what a shared room changes
is the **audience for disclosure**, never who is speaking and never who has authority. Authority
comes from the sender of the message being answered, not from the thread: a colleague in the
owner's thread gets the colleague path and cannot borrow the owner's rights.

For the owner in a non-DM context (`ctx.kind != "dm"`) the pipeline runs the owner tool loop with
`shared=True`:

- **Audience:** unchanged. A room has `has_unknown=True` until channel membership is verified, so
  recall and the pre-recall notes return public-level memories only, and past messages and
  documents are not searched. A private note from the DM does not appear; Ava says it is in the DM.
- **History:** this conversation (thread) only. The owner's other-channel DM turns are never in a
  room's prompt. Other people's messages in the thread are labelled "Another person in this
  channel, not your owner".
- **Not learned:** nothing from a room is saved to memory or the journal, and the second opinion
  and the playbook experiment are skipped.
- **Reply:** the system prompt tells Ava to address the owner by name.

| Action | In the DM | In a channel or thread |
|---|---|---|
| Recall (notes, messages, documents) | audience of the DM: private notes | public-level notes only |
| Web search and fetch | yes | yes |
| `create_reminder` | yes | yes (it echoes what the owner just wrote) |
| List, update, cancel reminders | yes | no, DM only (reminder text is private) |
| Gmail and calendar tools (read, draft, send, create, move, decline) | yes, writes still need approval | no, DM only |
| `propose_action` for `message_someone_else`, `publish_post`, creation kinds | queues an approval card | queues an approval card (it happens in the signed-in app) |
| `propose_action` for `grant_access`, `delete_memory` (need a fresh step-up) | refused unless step-up trust | refused: "ask me in a direct message" |
| `NEVER_VIA_CHAT` (settings, credentials, trust, export, delete data) | refused | refused |
| "remember that ..." and learning from the message | yes | no |
| Link codes | redeemed | never redeemed, never repeated |
| Unlinked or unconfirmed Slack user | stranger path / fixed reply | unchanged (unconfirmed: silent) |
| Any other person in the room | their own relation, as before | their own relation, as before |

Open: promoting a private memory to the channel ("want me to share it here?") would be a
disclosure write and is not built; Ava points to the DM. Slack replies in a thread that do not
@-mention Ava are not delivered to her (Slack `message` events in channels are ignored by design).
