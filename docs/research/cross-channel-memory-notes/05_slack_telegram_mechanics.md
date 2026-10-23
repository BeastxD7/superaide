# Slack and Telegram platform mechanics for identity, audience and visibility (as of 2026-10-23)

Method note: facts below come from fetched official pages (docs.slack.dev, slack.com terms, core.telegram.org, Google developers) unless marked. WebFetch summaries come from a small model, so exact quoted strings are the fetcher's extraction; re-verify any clause before legal reliance.

## Slack identity: user IDs, workspace vs Enterprise Grid, strangers, Slack Connect, profile fields

### Takeaway
A Slack user key is (team_id, user id), or the single global enterprise id on Grid; display names, usernames and emails are mutable or scope-gated and must not be identity keys. External/Slack Connect users appear with foreign ids and `is_stranger`/`team_id` fields that the design must read explicitly.

### Cited Findings
- User `id` is unique within its workspace; pair with `team_id` as the unique key; treat as opaque, do not parse structure. — [User object](https://docs.slack.dev/reference/objects/user-object)
- Within an Enterprise Grid org, all users have a single global ID beginning with U or W; the prefix carries no meaning. `enterprise_user.id` is preferred over root `id` when present; `enterprise_user.teams` lists the workspaces the person belongs to. Pre-migration IDs are mapped to the global ID; `migration.exchange` converts stored local IDs. — [Enterprise orgs](https://docs.slack.dev/enterprise/developing-for-enterprise-orgs), [User object](https://docs.slack.dev/reference/objects/user-object)
- `enterprise_id` is constant for workspaces in an org; docs recommend storing it and grouping data by it. `team_id` is the workspace the user is logged into for a channel and can vary by session. — [Enterprise orgs](https://docs.slack.dev/enterprise/developing-for-enterprise-orgs)
- `user_team` = team the message sender belongs to; `source_team` = workspace where a shared-channel message originated; `context_team_id` in outgoing dispatches (e.g. Block Kit actions) reflects the viewing user's perspective on a shared channel. — [Enterprise orgs](https://docs.slack.dev/enterprise/developing-for-enterprise-orgs)
- `is_stranger` is true if the user is in a different workspace than the app's token and not in a shared channel the app can see. Slack Connect docs: no single property decides external status; use `is_stranger` plus the user's `team_id`. Look up foreign users with `users.info` (they are absent from `users.list`). — [User object](https://docs.slack.dev/reference/objects/user-object), [Slack Connect](https://docs.slack.dev/apis/slack-connect/)
- Identify users by `id`, not `username`; `username` is not reliable and does not work with foreign users. Channel names can differ across teams, so key on channel ID. — [Slack Connect](https://docs.slack.dev/apis/slack-connect/)
- Mutable fields: `display_name` "can change at any time, so don't use it as a unique identifier"; `real_name` is editable and linked to first/last name; `name` is deprecated. — [User object](https://docs.slack.dev/reference/objects/user-object)
- `email` requires `users:read.email` (plus `users:read`); only admins can change it on paid teams, and changes notify old and new addresses. External members/strangers return no locale. — [User object](https://docs.slack.dev/reference/objects/user-object), [Slack Connect](https://docs.slack.dev/apis/slack-connect/)
- Flags: `is_bot` (Slackbot returns false), `is_app_user`, `is_restricted` (guest), `is_ultra_restricted` (single-channel guest), `deleted` (deactivated). — [User object](https://docs.slack.dev/reference/objects/user-object)
- Channel object has `context_team_id` and `connected_team_ids` (external workspaces via Slack Connect). Channel IDs can change when a channel becomes shared (G to C prefix; subscribe to `channel_id_changed`); a disconnected channel gets a new ID for invited orgs and the old one returns `channel_not_found`. Slack Connect supports up to 250 orgs. — [Channel object](https://docs.slack.dev/reference/objects/channel-object/), [Slack Connect](https://docs.slack.dev/apis/slack-connect/)
- Event envelope: `team_id`, `api_app_id`, `event_id` (globally unique), `event_context`, `authorizations` (one installation only: enterprise_id, team_id, user_id, is_bot, is_enterprise_install), `is_ext_shared_channel`, `context_enterprise_id`. Full authorization list via `apps.event.authorizations.list`. Outer `team_id` mirrors first `authorized_users` entry. Shared-channel events carry `connected_team_id`. — [Events API](https://docs.slack.dev/apis/events-api/), [Slack Connect](https://docs.slack.dev/apis/slack-connect/)

### Inferences
- Canonical Slack identity key should be (enterprise_id or team_id scope, user id) with the global Grid id when `enterprise_user` is present; store `team_id` and `user_team` on every observation because the same human has separate ids in separate non-Grid workspaces and no Slack-provided cross-workspace link exists outside a Grid org.
- Never trust display/real name or email alone to merge people; email also needs a scope and can be changed by admins.
- In shared channels, treat any sender whose `user_team`/team_id differs from the installing workspace as external by default.

### Gaps
- U vs W: only the Grid doc says both exist and carry no meaning; no primary doc explaining historical W usage (e.g. guests/Grid) found.
- `is_ext_shared_channel` appears in the Events API doc fetch; the `context_team_id` envelope field was described only as appearing in a sample payload, not defined. A search did not independently confirm `is_ext_shared_channel` naming (only `is_ext_shared` on channel_unshared). Verify against a live payload.
- Workflow-builder/workflow bot users: not researched from primary docs.

## Slack events, scopes and visibility

### Takeaway
Apps only see what subscriptions and scopes allow; a bot sees channel messages only if it is a member (or via @mention), DMs via `im:history`; edits and deletions arrive as `message_changed`/`message_deleted` and are the hook for deletion duty. Thread, Assistant-panel and Slack Connect details need explicit handling.

### Cited Findings
- Event to scope map: `message.channels` (channels:history), `message.groups` (groups:history, private channels), `message.im` (im:history, channel_type "im"), `message.mpim` (mpim:history). — [message event](https://docs.slack.dev/reference/events/message), [message.im](https://docs.slack.dev/reference/events/message.im)
- Apps receive only events matching their subscriptions and granted scopes; users/bots see only what they can access (e.g. private channels they belong to). Rate cap: 30,000 deliveries per workspace per app per 60 min. — [Events API](https://docs.slack.dev/apis/events-api/)
- Delivery: ack within 3 seconds; retries up to 3 times (immediate, 1 min, 5 min), headers `x-slack-retry-num`/`x-slack-retry-reason`; with Delayed Events hourly retries for 24h; without it events >2h late not delivered. Idempotency key: `event_id`. — [Events API](https://docs.slack.dev/apis/events-api/)
- `app_mention` needs `app_mentions:read`; fires on messages directly mentioning the bot user; not dispatched for DMs; if the app is mentioned in a conversation it is not in and is not invited, no event; a mention that invites it does generate one. — [app_mention](https://docs.slack.dev/reference/events/app_mention)
- Subtypes: `message_changed` (edited messages carry `edited` with editor user and timestamp), `message_deleted` (hidden subtype with `deleted_ts`; hidden subtypes do not appear in `conversations.history`), `bot_message`, `thread_broadcast` (page says `reply_broadcast` "no longer served"). Not all subtypes are delivered via the Events API. — [message event](https://docs.slack.dev/reference/events/message)
- Slack Connect: apps receive messages/events from external-team users once a channel is shared; bots can DM external users who share a channel; slash commands/message actions only work for users of the installing team; apps should default to exposing less in shared channels; to receive all shared-channel events need `channels:read`/`groups:read`, and handle `authorizations`. — [Slack Connect](https://docs.slack.dev/apis/slack-connect/)
- Assistant/agent features: `assistant:write` scope enables top-nav entry point and agent labeling; user messages in the assistant split view arrive as `message.im`; `app_context_changed` and `app_context` on `message.im`/`app_home_opened`; `thread_ts` is the unique conversation identifier; fetch history with `conversations.replies`. Current docs mention `agent_session_stopped`, `agent_session_title_changed`. — [Developing agents](https://docs.slack.dev/ai/developing-agents), [Developing AI apps](https://docs.slack.dev/ai/developing-ai-apps)
- `assistant_thread_started` payload (per search summary of sample): `assistant_thread` with `user_id`, `channel_id`, `thread_ts`, and `context` (channel_id, team_id, enterprise_id); context channel access must be checked with `conversations.info`; `assistant_thread_context_changed` follows later context changes. — [assistant_thread_started reference](https://docs.slack.dev/reference/events/assistant_thread_started), [api.slack.com sample](https://api.slack.com/events/assistant_thread_started)
- Slack Marketplace/internal-only RTS API: `assistant.search.context`; scopes `search:read.public` minimum, optional `search:read.private/mpim/im/files/users` (private/mpim/im need user token); bot-token calls need an `action_token` obtained from message or `app_mention` events; workspace guests cannot use apps with platform AI features; "must not store or copy any of the data retrieved", "may not use any of this data for training"; do not expose results to anyone who could not access them in Slack; Slack Connect calls return results only from that channel. — [Real-time Search API](https://docs.slack.dev/apis/web-api/real-time-search-api)
- Slack MCP server: `https://mcp.slack.com/mcp`, Streamable HTTP, confidential OAuth with user tokens, tools for search, read channels/threads, send messages, canvases, lists; only Marketplace-published and internal apps may use MCP; same rate limits as Web API; audit-loggable. — [Slack MCP server](https://docs.slack.dev/ai/slack-mcp-server)

### Inferences
- A member bot with `channels:history` receives every message in the channel; an @mention-only posture (`app_mentions:read` only) limits retention exposure and matches an audience-scoped design: only the mention message and thread the bot is asked about are seen.
- Memory design must key conversation on (team_id, channel, thread_ts) and store `ts` of each source message so `message_deleted`/`message_changed` events can retract or update derived memories.
- Slack Connect channels: audience = union of both orgs' members, so memories from them should default to the narrowest audience.

### Gaps
- `parent_user_id` and `thread_ts` semantics not found on the fetched message page; `reply_broadcast` described as no longer served only.
- `message_changed` payload structure (previous_message/message) not described on the fetched page.
- Whether bots see messages in private channels they are not members of: implied no (membership needed), not stated explicitly.
- Assistant thread message subtype (`assistant_app_thread`) was only a search hit; not fetched.
- Data residency: only a secondary search summary (Slack help/Trailhead listed regions, default US, membership lists stored outside region) - not primary-verified here.

## Slack API terms, rate limits, deletion, retention and legal holds

### Takeaway
Since 2025 Slack contractually bars bulk export, persistent indexes and LLM training on other organisations' API data; a design that stores Slack message content long term is at legal risk for distributed apps (own-workspace/internal apps are treated differently). Deletions and holds are not cleanly propagated to apps.

### Cited Findings
- Slack API Terms (effective 10 Oct 2025): bans using API Data to train an LLM; bans bulk export of Slack message and file data except by additional agreement; for Data Access/Real-Time Search API "you may not create persistent copies, archives, indexes or long-term data stores of other organisations' API Data", data must be minimised and deleted promptly; obtain explicit authorisation from installing organisation; limit use, processing and retention of others' API Data to the minimum necessary. No clause found specifically about honouring user deletions. — [Slack API Terms](https://slack.com/terms-of-service/api)
- 29 May 2025 rate-limit change: for commercially distributed non-Marketplace apps created on/after that date and new installs, `conversations.history` and `conversations.replies` drop to Tier 1 (1 request/min, max and default `limit` 15). Marketplace apps and existing installs unaffected; internal customer-built apps keep 50+/min and 1,000 objects. Updated API ToS effective 29 May 2025 for new apps, 30 Jun 2025 for existing apps. — [Changelog](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps/)
- Law-firm commentary (Hunton, 5 Aug 2025) reports the same restrictions (bulk export, persistent copies/indexes, LLM use). Secondary source. — [Hunton via search](https://hunton.sitepilot11.firmseek.com/insights/legal/salesforce-locks-down-slack-data-time-to-review-your-slack-api-terms)
- Legal holds: messages and files are preserved regardless of retention settings or member edits/deletes, retrievable by JSON export or Discovery API; Slack Connect conversations and emoji reactions not covered by holds. Under default retention (no revisions) deleted messages are not preserved; retention policies that delete data are permanent. — [Slack legal holds help](https://slack.com/help/articles/4401830811795-Create-and-manage-legal-holds), [Trailhead retention](https://trailhead.salesforce.com/content/learn/modules/data-protection-with-slack/manage-data-retention) (via search summary)

### Inferences
- For the Superaide-style single workspace internal app the Tier-1 limit and "other organisations'" restrictions may not apply, but a multi-owner product (each owner connects their own workspace) is a distributed app serving other organisations, so the terms apply; per-owner data must stay minimal, scoped to the installing organisation, and deletable.
- Safe pattern: store derived facts with source pointers (channel, ts), re-verify on read, delete on `message_deleted`, and never use content to train models.

### Gaps
- Whether API ToS "other organisations'" wording covers a bot storing its own installing workspace's messages: not resolved; needs legal review.
- Exact retention-API method names and scope were only from a secondary blog; not confirmed.
- No primary statement found on how retention/legal hold deletion is signalled to third-party apps.

## Telegram identity, chats, privacy mode, topics, deep links, business and managed bots

### Takeaway
Telegram's stable key is the numeric user id (up to 52 significant bits, use 64-bit ints); username is optional and mutable; privacy mode default-on limits a group bot to commands, replies and mentions. Business and Guest/Managed features in Bot API 9.x-10.x widen what bots can see and do.

### Cited Findings
- `User.id` and `Chat.id` can exceed 32 bits, have at most 52 significant bits; use signed 64-bit integer or double. `username` is optional; `is_bot` marks bots. — [Bot API](https://core.telegram.org/bots/api)
- Privacy mode (default on): bot sees commands addressed to it, `/start`-style general commands if it was last bot to post, inline messages via the bot, and replies to its messages; off, or bot is admin: receives all group messages; must be re-added after changing. Regardless of mode: service messages, all private chat messages, all channel messages where member. — [Bot features](https://core.telegram.org/bots/features)
- Deep links: `t.me/<bot>?start=PARAM` delivers `/start PARAM`; `?startgroup=` prompts group choice; allowed chars A-Z a-z 0-9 _ -, max 64; base64url for binary. Business "Manage Bot" sends `/start bizChat<user_chat_id>`. — [Bot features](https://core.telegram.org/bots/features)
- `message_thread_id` identifies a thread/forum topic in supergroups and private chats; `is_topic_message` true for topic messages. Topics in private chats: Bot API 9.3 (31 Dec 2025); bots can create topics in private chats: 9.4 (9 Feb 2026). — [Bot API](https://core.telegram.org/bots/api), [Changelog](https://core.telegram.org/bots/api-changelog)
- `sender_chat` is set when a message is sent on behalf of a chat (anonymous admins, linked channel auto-forwards), so `from` is not always the human. Forward origin may be a hidden user with only a name (`MessageOriginHiddenUser`). — [Bot API](https://core.telegram.org/bots/api)
- Updates: `getUpdates` and `setWebhook` mutually exclusive; webhook `secret_token` sent in `X-Telegram-Bot-Api-Secret-Token`; undelivered updates kept at most 24 hours; `allowed_updates` default excludes `chat_member`, `message_reaction`, `message_reaction_count`. `edited_message` exists; `my_chat_member` tracks bot's own status (in private chats only block/unblock). — [Bot API](https://core.telegram.org/bots/api)
- Deletion: bots can delete incoming messages in private chats, admin bots in groups; deletion only if message < 48 hours old (with exceptions). I found no ordinary-chat delete update in fetched material. — [Bot API deleteMessage](https://core.telegram.org/bots/api#deletemessage)
- Business bots: enable Secretary Mode; account owner chooses chats; rights like `can_reply`, `can_read_messages`; updates `business_connection`, `business_message`, `edited_business_message`, `deleted_business_messages`; sends use `business_connection_id`. Business API largest change in Bot API 9.0 (11 Apr 2025). — [Bot features](https://core.telegram.org/bots/features), [Changelog](https://core.telegram.org/bots/api-changelog)
- Managed bots: a manager bot creates/manages bots for owners via `t.me/newbot/{manager}/{new}`; `managed_bot` update; methods `getManagedBotToken`, `replaceManagedBotToken`, access-settings methods (Bot API 9.6, 3 Apr 2026). — [Bot features](https://core.telegram.org/bots/features), [Changelog](https://core.telegram.org/bots/api-changelog)
- 2026 versions: 9.5 (1 Mar), 9.6 (3 Apr), 10.0 (8 May: Guest mode, business bots without Premium, bot-to-bot business, `getUserPersonalChatMessages`), 10.1 (11 Jun), 10.2 (14 Jul: ephemeral messages visible to one user and the bot, Communities), 10.3 (24 Aug). — [Changelog](https://core.telegram.org/bots/api-changelog)
- Guest mode: a bot is summoned by `@botname` mention or reply in chats it has not joined; up to 3 guest bots per message; no access to message history or participant list. Bot-to-bot loops are a stated risk. — [Bot features](https://core.telegram.org/bots/features)
- Commands: up to 32 chars; command scopes are not visible in updates, so validate commands server-side. — [Bot features](https://core.telegram.org/bots/features)

### Inferences
- With privacy mode on, the group audience a bot can observe is only messages addressed to it; storing "who was present" from group traffic is impossible without privacy off/admin; membership via `chat_member` requires admin and explicit `allowed_updates`.
- Key identities as (telegram, user.id); treat `sender_chat` and hidden forward origins as non-identifiable, never assign to a person.
- Business connections: the audience is the account owner's customers; a separate scoped audience per `business_connection_id` is sensible.
- Telegram has no delete update for ordinary chats, so deletion duty cannot rely on events; provide a user-initiated "forget" path.

### Gaps
- BusinessConnection, BusinessBotRights, DeletedBusinessMessages and ManagedBotUpdated field definitions: the fetch did not reach those sections (page truncated); fields stated above come from the features page only.
- Whether Telegram user ids are stable across bots (yes in practice) is not explicitly stated in the docs fetched.
- Bot API terms on message data retention: not researched.

## Cross-platform identity linking, merge/split and takeover risk

### Takeaway
Link accounts only through proof of control of both sides (one-time code sent in-app via an already-trusted channel, or OIDC), never via display names; Telegram now offers OIDC login; Slack has no cross-workspace identity.

### Cited Findings
- Telegram Login is now OpenID Connect (Authorization Code with PKCE S256, `state`, `nonce`, JWKS verification of `id_token`, `iss` = `https://oauth.telegram.org`, `aud` = Bot ID); claims `sub`, `id`, `name`, `preferred_username`, optional `phone_number`. Legacy widget docs archived at `/widgets/login-legacy`. — [Telegram Login](https://core.telegram.org/widgets/login)
- Slack: display_name changes any time, email is admin-changeable and scope-gated, foreign users have foreign ids. — [User object](https://docs.slack.dev/reference/objects/user-object), [Slack Connect](https://docs.slack.dev/apis/slack-connect/)
- Slack Grid: one global id per person across org workspaces; separate workspaces outside a Grid org yield separate ids. — [Enterprise orgs](https://docs.slack.dev/enterprise/developing-for-enterprise-orgs)

### Inferences
- Best practice (my synthesis, not from a single primary source): one-time code delivered to the already-verified channel and entered in the new one; bind to immutable platform id; record link event with timestamp and method; require re-verification on phone/email change; keep links reversible (unlink must split the person node without deleting per-channel observations).
- Takeover vectors: recycled Telegram usernames, Slack admin-changed emails, deactivated and reactivated Slack accounts, shared devices; mitigate by never trusting username/email/name alone and by expiring links on `deleted` flag.
- Merge rule: auto-merge only on cryptographic proof; propose-merge (needs owner approval) for heuristics.

### Gaps
- No primary-source "best practice" document for cross-channel linking was fetched; this is reasoning.
- Slack Sign in with Slack (OIDC) details not fetched.

## Email and calendar as future channels (brief)

### Takeaway
Gmail groups messages into threads by `threadId` using RFC 2822 headers and subject match; participants must be parsed from headers.

### Cited Findings
- Gmail threads: `threadId` groups replies; to join a thread a message must set `threadId`, matching `References`/`In-Reply-To` headers and matching `Subject`. Threads cannot be created directly. — [Gmail threads guide](https://developers.google.com/workspace/gmail/api/guides/threads)

### Inferences
- Email identity key is the address (mutable aliases, forwardable, spoofable From); treat as weaker than platform-id channels; sender verification needs SPF/DKIM/DMARC results.

### Gaps
- Gmail history IDs, push notification/watch, participant fields and Calendar attendee semantics were not covered in sources fetched.
