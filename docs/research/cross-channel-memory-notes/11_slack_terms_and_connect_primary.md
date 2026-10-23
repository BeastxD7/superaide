# Slack Terms, Slack Connect, Telegram and Google rules for storing message content as memory (primary sources)

Not legal advice. Items marked [LAWYER] need counsel.

Method: pages were downloaded raw with curl and converted to text locally (no summariser model), then read in full and grepped. Quotes below are verbatim from that text. One early WebFetch (summariser) pass on the API ToS was discarded and redone from raw text. Fetched 2026-10-23.

Pages that could NOT be used:
- slack.com help-centre articles (message retention, "A guide to Slack Connect", "Use apps in shared channels", Discovery): the server returned only the generic "How to use Slack" quick-start page. No retention-setting or admin text could be read.
- https://docs.slack.dev/admins/discovery-api/ and /admins/managing-retention: 404 "Page Not Found".
- https://api.slack.com/developer-policy, /changelog/..., /docs/rate-limits redirect (302) to docs.slack.dev; the redirected pages were fetched fine.
- The Telegram Bot API page (core.telegram.org/bots/api) is 490 KB; I grepped for specific field names rather than reading it end to end.
- Telegram ToS page shows no "last updated" date in the text I extracted.

## 1. May Ava keep a persistent searchable copy (text, embeddings, summaries) of messages from the installing workspace's own channels and DMs?

### Takeaway
The primary text does not forbid it, but it permits it only conditionally. Slack's "persistent copies" ban is scoped to the Data Access API and Real-Time Search API, not the Events API. The general rule is "minimum necessary" retention with explicit authorisation from the installing organisation, no LLM training, and deletion within 14 business days of uninstall. Whether embeddings and summaries count as "API Data" is not stated. The Marketplace guideline "DON'T store any Slack data" is printed under the Agent/Assistant UI heading, so it may bind a Marketplace-listed Ava. Treat zero-copy as the safe direction if you want to be listed. [LAWYER] for derived data and for sending Slack text to the Claude API.

### Cited Findings
- Effective date of the API Terms: "Effective: 10th October 2025" — [Slack API Terms of Service](https://slack.com/terms-of-service/api)
- Developer Policy: "Effective Date: December 10, 2024" — [Slack App Developer Policy](https://docs.slack.dev/developer-policy/)
- Definition: "‘API Data’ includes messages, files, metadata and other data obtained from Slack APIs." — API Terms, section "Data usage by third parties"
- Authorisation and minimisation: "If you offer your Application for use by others outside your organisation, you must obtain explicit authorisation from the organisation installing your Application for the use, processing and storage of API Data." and "You must limit your use, processing and retention of API Data from others outside your organisation to the minimum necessary to develop, test, operate and support your Application’s functionality, in accordance with the Documentation." — API Terms, "Data usage by third parties"
- Prohibitions on providers of externally offered apps: "you may not: (A) use API Data to train a large language model; (B) bulk export Slack message and file data except where expressly allowed by an additional agreement, such as by Applications using the Discovery API for approved security and compliance use cases; or (C) use data collected from one organisation to directly benefit a different organisation or any third party." — API Terms, "Data usage by third parties"
- The persistent-copy ban is API-specific: "When using these APIs as a third-party Application provider, you may not create persistent copies, archives, indexes or long-term data stores of other organisations’ API Data. Limited and temporary handling, caching or storage of other organisations’ API Data retrieved is allowed only to the extent that it is essential for the immediate operation, performance or delivery of your Application, or as required by law. Such data must be minimised and deleted promptly once those specific needs are met, and may not be used for product improvements." — API Terms, "Data Access API and Real-Time Search API" (the preceding sentence defines the scope as those two APIs; the Events API is not named).
- "Your Application’s use of these APIs should be transparent to and reasonably expected by users of the Services; background data collection or scraping for data unrelated to user queries is prohibited." — same section
- The single-organisation carve-out: "These restrictions do not apply if your Application and any products or services connected to it were created for use only by a single third party." — API Terms, "Commercial distribution"
- Developer Policy "Use of Data" (applies to all apps; the policy says it governs the Marketplace and "Developers are required to comply with this Policy"): prohibits "Collecting, storing, and/or using Data without obtaining proper consent"; "Using Data to train an LLM under any circumstances"; "Renting, selling or sharing Data with third parties under any circumstances"; "Exploiting Data to create User profiles other than that which is necessary for the Application to function"; "Combining Data with data gathered from other sources for any purposes unrelated to the use of the Application"; "Accessing Data for surveillance purposes."; "Otherwise exploiting Data in a way not approved by Slack and not disclosed to and permitted by Users. You may, however, use Data that is both aggregated and anonymized for purposes of analytics and development related to the Application" — [Developer Policy](https://docs.slack.dev/developer-policy/)
- Definition of "Data" in that policy: "data, information or content uploaded, posted, transmitted or otherwise made available by Users via the Services, including messages, files, comments, profile information, metadata and token data." — Developer Policy preamble
- Security: "Data must be stored and served using strong encryption." and "Your application must include a publicly-available and easily accessible privacy policy that explains how the Application collects, uses, processes and stores Data, and what control Users have over their Data" (policy wording: "Your Application must include a publicly-available and easily accessible privacy policy...") — Developer Policy, "Security" and "Use of Data"
- Marketplace guidelines, enhanced review: scopes "search:read.*, *:history, files:read, and canvases:read" trigger "an enhanced review" — [Marketplace guidelines](https://docs.slack.dev/slack-marketplace/slack-marketplace-app-guidelines-and-requirements)
- Marketplace guidelines, AI: "🚫 DON'T use Slack data to train LLMs." and under "AI components": disclose "How long users' data is retained and how it is being used by the LLM", "LLM data tenancy", "LLM data residency". Also "unlisted apps" and apps that "use Slack data to train Large Language Models (LLMs)" appear in the list of apps unsuitable for the Marketplace. — Marketplace guidelines
- Marketplace guidelines, under "Agent or Assistant UI": "🚫 DON'T store any Slack data you obtain. Store metadata instead and pull in data in real time if needed, i.e. zero-copy." — Marketplace guidelines (placed under the heading "Agent or Assistant UI"; the text does not say whether it applies to apps without that UI)
- Slack's own pitch for the Real-time Search API: "This approach enables third-party applications to retrieve relevant Slack data without storing customer information on external servers." — [RTS API](https://docs.slack.dev/apis/web-api/real-time-search-api/)
- Slack's rate-limit announcement says bulk indexing is the concern: "bulk downloads of customers' conversational data for indexing and querying pose risks of exposing sensitive information and undermining Slack access controls, such as private channel membership." — [29 May 2025 changelog](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps)
- MCP: "Only apps published in the Slack Marketplace and internal apps can use MCP at this time; unlisted apps are prohibited from using MCP." — [Slack MCP server](https://docs.slack.dev/ai/mcp-server/)

### Inferences
- A per-owner Ava that receives messages via the Events API and stores them is not textually covered by the "persistent copies" ban. It is covered by "minimum necessary ... to operate ... your Application’s functionality", which is a judgement call for a memory product. Embeddings and summaries are "derived" data; the text is silent, and the Developer Policy's "create User profiles other than that which is necessary" is the closest clause.
- If Ava is offered to many separate organisations (the "anyone can hire their own Ava" model), the "Distribution beyond your organisation" clauses apply. If users pay or it connects to a paid product, "Commercially Distribute" requires Marketplace or a partner agreement.
- Sending Slack text to a third-party model API (Anthropic) is arguably "sharing Data with third parties" under the Developer Policy. The ToS speaks of "use, processing and storage" needing the installing organisation's explicit authorisation. The Marketplace asks for disclosure of the model and retention. [LAWYER]

### Gaps
- No clause says whether embeddings or LLM-generated summaries are "API Data" or "Data".
- No clause says whether events-API storage for memory is "minimum necessary".
- The Marketplace "DON'T store any Slack data" line is under the Agent/Assistant heading; I could not determine whether it is app-wide.
- A customer who builds and installs their own app in their own workspace (Slack's "internal customer-built"): the ToS "outside your organisation" clauses do not trigger on their face. Ava installed by a single organisation as a custom app is a separate situation from Ava as a hosted multi-tenant product. [LAWYER]

## 2. May Ava keep such a copy of messages from an external organisation in a Slack Connect channel?

### Takeaway
The primary text has no Slack Connect-specific data-use clause. The API Terms do not mention Slack Connect. The general clauses are the only guide, and two bite hard: "use data collected from one organisation to directly benefit a different organisation" and (if the Data Access / RTS APIs are used) "other organisations’ API Data". Authorisation is given by the installing organisation only. Counsel is needed.

### Cited Findings
- Events from the partner arrive at the installing app: "Your app will receive messages and events from users on external teams." and "It may be helpful for your app to note the connected_team_id, as it will begin receiving messages and events from users on that external team." — [Understanding Slack Connect](https://docs.slack.dev/apis/slack-connect/)
- "When an external member messages you, the event payload will contain a team_id property that indicates the workspace where the message came from." — same page, "Beware sharing users' data"
- Slack's own advice: "As a rule of thumb, your app should default to exposing less information in shared channels to protect your users' data." and "Does your app provide access to sensitive, internal data? You must ask for confirmation before sharing this information to an external partner." — same page
- "(C) use data collected from one organisation to directly benefit a different organisation or any third party." — API Terms, "Data usage by third parties"
- Authorisation comes from "the organisation installing your Application" — API Terms. Slack Connect page: "If your app is installed on a workspace that shares a channel with another workspace, your app could be authorized in one workspace but not authorized in the other."
- "Data retention settings may differ between teams." — Slack Connect page, "Channel settings"
- When unshared: "each workspace can still access channel history for all previous messages and activity." — Slack Connect page
- The RTS API treats Slack Connect conservatively: "If the Real-time Search API is called from a Slack Connect channel, it will return results from only the channel where it was invoked. This prevents users from seeing data they might otherwise not have access to." — RTS API page
- Developer Policy: "Collecting, storing, and/or using Data without obtaining proper consent" is prohibited; "User" is defined as including "anyone whose Data is exposed to or used by the Application" — Developer Policy preamble and "Use of Data". This definition arguably brings the partner organisation's people within the consent clause even though they did not install the app.

### Inferences
- Storing partner messages and then using them for the benefit of the partner is the least problematic case. Using them in conversations with the installing organisation's other people (cross-org memory) maps onto the ToS clause "use data collected from one organisation to directly benefit a different organisation". Partner data used to benefit the installing org is a reading counsel should settle.
- Safest design to survive review: tag every stored item with `team_id` and `is_ext_shared_channel`, keep external-origin items scoped to that channel and never merge into general memory, and make deletion by origin team trivial.

### Gaps
- No primary text states whether the partner organisation's consent or notice is needed. [LAWYER]
- No text addresses GDPR controller/processor roles for external-member data. [LAWYER]
- Slack help-centre pages on Slack Connect admin controls (e.g. whether the partner's admin sees which apps are in the channel) could not be read (see top).

## 3. What deletion duties follow an edit, a delete or an uninstall?

### Takeaway
Slack documents the events and says clients should mirror them. The only explicit time-bound duty is the Developer Policy's 14 business days after uninstall/discontinuation and "Ignoring a User’s request for deletion" is prohibited. No text says that embeddings or summaries derived from a deleted message must be purged; treat that as the safe reading.

### Cited Findings
- message_deleted: "A message_deleted message is sent when a message in a channel is deleted, usually via the chat.delete method. The deleted_ts property gives the timestamp of the message that was deleted. If clients find an existing message with the same deleted_ts and channel, the existing message should be removed from the local model and UI. The original message will no longer return in history calls." — [message_deleted](https://docs.slack.dev/reference/events/message/message_deleted/). Required scopes: channels:history, groups:history, im:history, mpim:history.
- message_changed: "A message_changed message is sent when a message in a channel is edited using the chat.update API method. The message property contains the updated message object. ... When clients receive this message type, they should look for an existing message with the same message.ts in that channel. If they find one, the existing message should be replaced with the new one." Also: "This event can also sometimes be triggered by our automatic language detection" — [message_changed](https://docs.slack.dev/reference/events/message/message_changed/)
- file_deleted: "This event is not raised if the reason for a file's removal is due to a workspace's file retention policy, as opposed to a user deleting the file." — [file_deleted](https://docs.slack.dev/reference/events/file_deleted/). So retention-policy expiry of files is not signalled to the app.
- tokens_revoked: "When your app's API tokens are revoked, the tokens_revoked event is sent via the Events API to your app if it is subscribed." — [tokens_revoked](https://docs.slack.dev/reference/events/tokens_revoked/). It carries user IDs, not tokens; key by team_id.
- app_uninstalled: "You should also receive tokens_revoked events for revoked tokens however the order of the app_uninstalled and tokens_revoked events are not guaranteed to be sequential (you could receive a tokens_revoked event after app_uninstalled). An app is not uninstalled until its final user and bot tokens are revoked." — [app_uninstalled](https://docs.slack.dev/reference/events/app_uninstalled/)
- Events stop on uninstall: "If a user uninstalls your app (or the tokens issued to your app are revoked), events for that user will immediately stop being sent to your app." — [Events API](https://docs.slack.dev/apis/events-api/), "App revocation"
- Developer Policy: "Ignoring a User’s request for deletion. When a User deletes your Application or if you discontinue your Application you must delete all associated Data within 14 business days" (listed as prohibited conduct) — [Developer Policy](https://docs.slack.dev/developer-policy/), "Use of Data"
- API Terms, termination: "Upon termination of the Contract, you will promptly destroy copies of any documentation and any other Slack information in your possession or control that was received under the Contract."
- Data breach: "If Data is breached, exposed, exploited, or otherwise compromised through your Application or company, you must inform all affected Users and Slack immediately." and the ToS: notify "security@slack.com" of "any security incident resulting in unauthorised access to or loss of Slack data".
- Marketplace guideline list for leaving: "Deleting and revoking any tokens your app generated" — Marketplace guidelines.
- Slack's data page notes: "members can edit and delete the messages that they send." and "Workspace owners and admins can delete anyone’s messages in public channels" — [Slack data management](https://slack.com/trust/data-management)

### Inferences
- Implement tombstoning keyed on (team_id, channel, ts), cascading to chunks, embeddings, summaries and any memory fact whose only source is that message. The message_changed payload should replace text and trigger re-embedding.
- Because file retention expiry is not evented and Slack Connect partners can have different retention, a periodic reconciliation job (re-fetch by ts) is needed for the cases where events are missing. Rate limits (section 4) constrain this.
- On app_uninstalled or tokens_revoked: start a 14 business day purge clock for that team_id, handle either event order, and purge by team_id.

### Gaps
- No Slack text says that derived data (embeddings, summaries, extracted facts) must be deleted when a source message is deleted. [LAWYER] (GDPR erasure analysis).
- No text says whether message_deleted events are delivered when the partner organisation's retention policy removes a message in a Connect channel. Sandbox test.
- Slack retention-settings help pages and Discovery API docs could not be read.

## 4. What do the rate limits imply for ingestion (events vs history)?

### Takeaway
Ingest on arrival from the Events API. A distributed, non-Marketplace app's new installs get conversations.history/replies at 1 request per minute and 15 messages per request, which makes history backfill unusable. An internal customer-built app is exempt. Ava's category depends on what "commercially distributed" means for how she is offered.

### Cited Findings
- Scope: "This rate limit reduction will apply only to applications that are commercially distributed outside of the Marketplace (also called “unlisted” apps). It will immediately impact affect new unlisted applications and new installations of existing unlisted non-Marketplace applications. The new rate limits will be updated to allow 1 request per minute and will return a maximum of 15 objects per request." — [29 May 2025 changelog](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps)
- "The new rate limits will not be applied to existing installations of unlisted, distributed applications published outside the Marketplace."
- "Internal customer-built applications are not impacted by these changes. For Custom apps, the conversations.history and conversations.replies API methods are limited to 50+ requests per minute. The maximum and default values for the limit parameter are 1,000 objects."
- "No, internal customer-built apps are not impacted and retain their current, higher rate limits. The new rate limits only apply to commercially distributed applications outside the Marketplace."
- Date note on the page: "This page has been updated to clarify the rate limits for existing internal customer-built apps." Terms effective: "immediately for applications created on or after May 29, 2025. They will go into effect on June 30, 2025 for apps created before May 29, 2025."
- Definition of distribution: "By ‘Commercially Distribute’, we mean any situation where your Application integrates with Slack APIs and users could pay fees for your product, service or features – whether it’s a direct charge to access the App, a ‘freemium’ model where some features are gated, or a free App that connects to a paid product or service." — [API Terms](https://slack.com/terms-of-service/api), "Commercial distribution". The same section says restrictions apply "regardless of whether you publish an off-the-shelf Application or you provide customers with a custom Application or Application template that connects to other products, services or features."
- Developer Policy: "Apps intended for commercial distribution at scale should be submitted for Slack Marketplace review", and it prohibits "listing an Application in the Slack Marketplace and also offering mutual customers a parallel unlisted or custom Application (distributed beyond your organization) that violates our Slack Marketplace app guidelines."
- Slack's stated alternative: "consider the other ways the platform may provide to get the kind of focused, contextual information an app typically requires, such as the Events API or the forthcoming Real-time Search API." — changelog FAQ
- General limits table: Web API Tier 3 "50+ per minute"; "Events API events: 30,000 deliveries per workspace/team per app per 60 minutes"; "When a workspace generates more than 30,000 events, you'll receive an informative event called app_rate_limited" — [Rate limits](https://docs.slack.dev/apis/web-api/rate-limits)
- Events delivery: "Your app should respond to the event request with an HTTP 2xx within three seconds." Retries at about immediately, 1 minute, 5 minutes. "The Events API is a best-effort system, and event delivery could be delayed during incidents. By default, we will not attempt to deliver an event that is more than two hours late. If the delayed events feature is enabled, we will attempt to deliver events regardless." (Delayed Events: hourly retries for 24 hours after the first three.) — [Events API](https://docs.slack.dev/apis/events-api/)
- RTM (legacy): "Message delivery to your app is not rate limited over RTM." — Rate limits page (legacy; not recommended).
- Socket Mode is not covered in the pages I fetched; not tested.

### Inferences
- Store on arrival; treat history as a rare, small gap-filler only. For an unlisted, commercially distributed Ava, backfill at 15 messages/minute is impractical, so a "learn from the past 6 months of the channel" feature is unavailable.
- Whether Ava is "commercially distributed" turns on whether users could pay (including freemium or "a free App that connects to a paid product"). A multi-owner hosted Ava likely is. A self-hosted Ava each owner installs as their own custom app is closer to "internal customer-built", but the ToS says the restrictions apply when you "provide customers with a custom Application or Application template". [LAWYER]
- Missed events (outage over two hours, 30,000/hour cap) cannot be repaired by history at 1/minute; design for idempotent processing by event_id and a catch-up for only small gaps.

### Gaps
- The text does not say whether an app installed by one workspace's admin for that workspace's own use, built by a third-party vendor, counts as "internal customer-built". The changelog does not define it beyond "Internal customer-built applications"/"Custom apps". [LAWYER] / ask Slack.
- Exact limits for conversations.history on Marketplace apps were not on the pages fetched (the changelog says Marketplace apps are unchanged).

## 5. Is a bot in a Slack Connect channel visible to / receiving events from the partner, and what can it see?

### Takeaway
Yes on both counts. The partner's users in the shared channel can see and interact with the bot, and the bot receives their messages, tagged with their team. It sees what the channel contains; it does not see the partner's other channels or DMs unless it is present in them.

### Cited Findings
- "Bot users are accessible to all users on the workspace where your app is installed, and any external members in a channel between organizations where your bot is also present." — [Understanding Slack Connect](https://docs.slack.dev/apis/slack-connect/), "Beware sharing users' data"
- "All workspaces involved in a connected channel can read and send messages, share files, and access the history of shared channels."
- Events: `channel_shared` — "is sent to all event subscriptions when a new shared channel is created or a channel is converted into a shared channel. It's also triggered when an external workspace is added to an existing shared channel. The connected_team_id value is the team ID of the workspace that has joined the channel." and "Note that this ID may start with E, indicating that it is the ID of the organization" — [channel_shared](https://docs.slack.dev/reference/events/channel_shared/). `channel_unshared` — "is sent to all event subscriptions when an external workspace has been removed from an existing shared channel. The previously_connected_team_id value is the team ID of the workspace that has been removed ... The is_ext_shared value is true if the channel is still externally shared, and false otherwise." — [channel_unshared](https://docs.slack.dev/reference/events/channel_unshared/). Scopes channels:read / groups:read; "supported as a bot user subscription".
- Subscription scope: "To receive all shared events for channels or groups in a workspace, your app will need the channels:read or groups:read scopes respectively. To receive only shared events for channels and groups your bot user is in, your app just needs the bot scope."
- Event envelope fields: "is_ext_shared_channel — Indicates whether the event occurred in an externally shared channel; i.e., a channel shared between two different Slack workspaces." and "context_enterprise_id — The enterprise org through which your app is receiving the event (i.e., where the app is installed)." The example envelope also carries `context_team_id`. — [Events API](https://docs.slack.dev/apis/events-api/)
- Authorized users: "authorized_users ... For each user in the authorized_users property: the app has a valid, correctly scoped token associated with that user, and the event happened inside a channel that the authorized user was a member of." "The team_id property on the event's outer payload will mirror the first element in the authorized_users array." — Slack Connect page ("authorizations" in current Events API wrapper; the page uses the older name authorized_users).
- conversations.info returns `is_ext_shared`, `context_team_id`, `shared_team_ids`, `is_pending_ext_shared`, `pending_shared`, `pending_connected_team_ids`; sample: `"is_ext_shared": false, "shared_team_ids": ["T123ABC456"]` — [conversations.info](https://docs.slack.dev/reference/methods/conversations.info/). Slack Connect page: "The conversations.info method will provide additional information on the workspaces connected to the shared channel and the ID of the host workspace." and "Use the is_ext_shared, is_private, and is_mpim flags exclusively to determine the privacy and type of a given channel. Beware of is_shared, which also includes channels shared between multiple workspaces in the same organization."
- External users: "The user type object (returned by methods such as users.info) provides additional information to identify external members, while withholding some information your app may expect." "If the user is a stranger who isn't in any shared channels, the is_stranger flag is set true." "For external members and strangers, their profile data will not contain any locale information, even if you pass the include_locale flag." "External members are also returned in the conversations.members API response." — Slack Connect page. users.info: "When looking up a user belonging to a foreign workspace party to a shared channel, you'll find an is_stranger boolean attribute." — [users.info](https://docs.slack.dev/reference/methods/users.info/). Email needs "users:read.email and users:read" (page does not say external emails are returned).
- Do not rely on names: "the username attribute cannot be relied upon as a unique identifier, and will not work with "foreign" users via the API."
- DMs: "A bot user is able to DM users on all connected workspaces, as long as users are in a shared channel together."
- Slash commands: "Slash commands and message actions are not shared — they are limited only to the team that has installed the app to their team workspace." and "link_shared events are not delivered when an external member shares a link that matches your app's unfurling domain, unless the app is installed in their workspace."
- Channel IDs change: "If the channel is set to private, the ID prefix may change from G to C ... Subscribe to the channel_id_changed event" and after unshare "the channel in the disconnected workspace will be assigned a new ID, while the host workspace keeps the original channel ID."
- Interaction check: "Check that the originating user is part of the authorized_users property in the interaction payload to ensure they're part of the installing authorization."
- Marketplace: the `conversations.connect:write` scope triggers "enhanced review" — Marketplace guidelines.

### Inferences
- Resolve identity by (team_id, user_id), never by name. An external member's `team_id` differs from the installing workspace's; treat these as untrusted and never grant manager or colleague authority from them.
- The bot is visible to the partner's members in that channel, so memory-based replies there can leak installing-org facts to the partner. This is the main audience-gate risk Slack itself warns about.

### Gaps
- The text does not say whether the partner's workspace admin is notified or can restrict a specific app in the shared channel. Help pages unreadable. Sandbox test.
- The text does not say if an external user's display name/email is returned for a stranger; the users.info page states only that some information is withheld.
- Whether message_changed / message_deleted events for partner users' edits are delivered to the installing app: implied by "receive messages and events" but not stated. Sandbox test.

## 6. Telegram (Bot Platform ToS, Bot API) 

### Takeaway
Telegram's Bot Platform terms are stricter on retention than Slack's text. They require deletion on request, deletion when retention is unnecessary, and deletion when the bot ceases. They forbid collection aimed at AI models. GDPR compliance is expressly the developer's job.

### Cited Findings
- 4.2 Data Storage and Retention: "Telegram imposes certain obligations regarding the degree to which you may continue to retain user data over time. ... you must, without undue delay: (a) Delete user data upon their (or our, as the case may be) request that you do so; (b) Delete user data when retention thereof becomes unnecessary to operate your TPA or to fulfill any other obligation as the case may be and as expressly agreed between you and your users; (c) Delete all user data obtained through or in connection with Bot Platform upon the cessation of your TPA's operations therein, except for data that users have expressly agreed can be retained, which may be kept until such agreement remains in effect; (d) Delete all user data obtained through or in connection with Bot Platform in response to a lawful request from an authorized legal entity" — [Telegram Bot Developer Terms](https://telegram.org/tos/bot-developers)
- 4. Privacy: "Telegram only stores the data it needs to function as a secure and feature-rich messaging service, and your TPA are required to do the same." and "all TPA must be bound by a privacy policy that is easily accessible to their users, detailing what data they store, how they collect it, and for what purpose."
- 4.3 Data Scraping: "You agree not to use your TPA to collect, store, aggregate or process data beyond what is essential for the operation of your services. Always prohibited uses include any form of data collection aimed at creating large datasets, machine learning models and AI products, such as scraping public group or channel contents."
- Security: "Make sure user data is always encrypted at rest and stored separately from its encryption key"
- Telegram Business chatbots: "(ii) If applicable and without limiting 4.2., clearly state what private user data you will retain, how long you will store it and for what purpose; (iii) Never use message contents, files or other data you obtained or processed through Telegram Business for any other purpose than providing your services as a business Chatbot; (iv) ... never disclose message contents, files or other data you obtained or processed through or in connection with Telegram Business to third parties (including third-party APIs) without the user's authorization" — same ToS (the subsection number was not captured)
- GDPR: "you will comply with all applicable privacy laws and regulations, such as the European Union’s General Data Protection Regulation (GDPR). It is your responsibility to determine whether or not such laws apply to your TPA, and if applicable, how to meet their requirements, and Telegram will not be liable if you fail to do so." — same ToS
- Telegram may remove data on its side: "Telegram may delete or make inaccessible whole chats, messages, media and files sent to and from your TPA at any time."
- Privacy mode: "By default, all bots added to groups run in Privacy Mode and only see relevant messages and commands: Commands explicitly meant for them ... Replies to any messages implicitly or explicitly meant for this bot." "All bots will also receive, regardless of privacy mode: All service messages. All messages from private chats. All messages from channels where they are a member." "Privacy mode is enabled by default for all bots, except bots that were added to a group as admins (bot admins always receive all messages). It can be disabled so that the bot receives all messages like an ordinary user (the bot will need to be re-added to the group for this change to take effect)." — [Telegram Bot Features](https://core.telegram.org/bots/features). `can_read_all_group_messages`: "True, if privacy mode is disabled for the bot. Returned only in getMe." — [Bot API](https://core.telegram.org/bots/api)
- message_thread_id: "Unique identifier of a message thread or forum topic to which the message belongs; for supergroups and private chats only" — Bot API, Message object.
- Business: the Update object includes `edited_business_message` ("New version of a message from a connected business account") and `deleted_business_messages` ("Messages were deleted from a connected business account"); methods take `business_connection_id`. — Bot API. Telegram Privacy Policy: "the bot will have access to all messages, media and files contained in the private chats you allow it to manage." — [Telegram Privacy Policy](https://telegram.org/privacy), section 6 (Business)
- The Bot API has `edited_message` updates; a bot learns of edits but, as far as I read, regular (non-business) bots get no delete event for ordinary messages. I did not find a statement either way in the ToS; this is an inference from the Update field list.

### Inferences
- Telegram's "delete when retention thereof becomes unnecessary" is a stricter duty than Slack's text. A user "forget me" command and a retention schedule are needed.
- Business-connected chats carry the "no other purpose" and "no third-party APIs without authorisation" limits, which bear on sending content to a model provider. [LAWYER]

### Gaps
- Telegram ToS "last updated" date was not captured. The standard "Privacy Policy for Bots and Mini Apps" text was not read.

## 7. Google / Gmail (Limited Use)

### Takeaway
Gmail scopes allow "generative AI summaries" as an approved use, but the Limited Use rules bar using the data to train models beyond that user's own personalised feature, bar human reading by default, and the same policy says "permanent copies" are prohibited. A persistent cross-source memory built from Gmail needs careful scoping. 

### Cited Findings
- Limited Use: "Limit your use of data to providing or improving your appropriate use case or features that are visible and prominent in the requesting application's user interface." "Transfers of data are not allowed, except: To provide or improve your appropriate use case or user-facing features ... and only with the user's consent; For security purposes ...; To comply with applicable laws and/or regulations; or, As part of a merger, acquisition or sale of assets ..." "Do not allow humans to read user data, unless: You have obtained and documented the user's explicit consent ...; The data (including derivations) is aggregated and anonymized ...; It's necessary for security purposes ...; or, To comply with applicable laws and/or regulations." — [Workspace API User Data and Developer Policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy) (page: "Last updated 2026-09-03 UTC")
- Model training: "Transferring, selling, or using user data to create, train, or improve a machine learning or artificial intelligence model beyond that specific user’s personalized model for the appropriate use case or user-facing feature."
- Permanent copies: "Remember, our Terms of Service prohibits the scraping, building databases (including databases for model training purposes), or otherwise creating permanent copies of Google User data. This includes keeping cached copies longer than permitted by the cache header." — same page, immediately after the Limited Use disclosure requirement.
- Disclosure: "The use of information received from Google Workspace scopes will adhere to the Google User Data Policy, including the Limited Use requirements." (example required statement). "You must provide user help documentation that explains how users can manage and delete their data from your app or service."
- Approved Gmail uses: "Applications that enhance the email experience for productivity purposes (such as applications for customer relationship management, delayed sending of email or mail merge, or providing generative AI summaries)". Not allowed: "Applications that export email on a one-time or manual basis."
- Restricted scopes need security assessment: "Applications accessing Restricted Scopes must demonstrate that they adhere to certain security practices." and annual assessment ("CASA") — same page; older [API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy) (Last updated February 15, 2024): "Note: If your app is only used by users within your own domain, then these requirements do not apply."
- The older policy lists the same Limited Use bullets, including "Don't allow humans to read the data" absent consent.

### Inferences
- Ava's per-owner memory of that owner's mail, used only for that owner's Ava, fits "that specific user’s personalized" and "user-facing features", but the "permanent copies" sentence is in tension with a durable memory store. [LAWYER]
- Cross-owner or cross-person use of Gmail-derived memory (answering a colleague from the owner's email) is a "transfer" and probably not within the exceptions; keep it out unless the owner consents per use.

### Gaps
- The "permanent copies" sentence is a note, not a numbered requirement; its exact legal reach is unclear.
- Google Chat, Drive and Calendar rules were not reviewed in full.

## 8. Answers to the six questions, plainly

1. Own workspace (Slack): Probably yes via the Events API with explicit authorisation from the installing org, minimum necessary retention, encrypted storage, no LLM training, 14 business day purge on uninstall. The "persistent copies, archives, indexes" ban is written for the Data Access and RTS APIs. Embeddings and summaries are not addressed. The Marketplace line "DON'T store any Slack data you obtain" (under the Agent/Assistant heading) points against it for a listed app. Sending the text to a model API needs disclosure and counsel. [LAWYER]
2. External org in Slack Connect: Technically received and storable. No Connect-specific rule exists. Risky clauses: "use data collected from one organisation to directly benefit a different organisation or any third party", "other organisations’ API Data", and "anyone whose Data is exposed to or used by the Application". Keep external items scoped to the channel and origin team. [LAWYER]
3. Deletion: mirror message_deleted (remove) and message_changed (replace); file retention-policy removals are not evented; on tokens_revoked / app_uninstalled delete all data for that team within 14 business days; cascade to embeddings and summaries (no text requires it, but it avoids the problem).
4. Rate limits: ingest on arrival. Internal customer-built apps keep 50+/min and 1,000 per request on history/replies. New installs of commercially distributed non-Marketplace apps get 1/min and 15 per request. Events API: 30,000/hour/workspace/app, ack within 3 seconds, best-effort.
5. Connect visibility: Yes, partner members in the channel can see and use the bot (except slash commands), and the bot receives their messages with their `team_id`; `is_ext_shared_channel` flags the event, conversations.info gives `is_ext_shared`, `shared_team_ids`, `context_team_id`, users have `is_stranger`.
6. Not answered by primary text: see the sandbox plan below.

## 9. Needs a lawyer (concrete list)
- Whether embeddings, summaries and extracted facts are "API Data"/"Data" and subject to deletion on source deletion (also GDPR erasure).
- Whether Events API storage of messages for memory is "minimum necessary ... to operate ... your Application’s functionality".
- Whether sending Slack text to Anthropic is "sharing Data with third parties" (Developer Policy) and what disclosure Slack expects.
- Whether a multi-owner hosted Ava is "Commercially Distribute[d]" and so needs the Marketplace; whether each owner installing their own custom app qualifies as "internal customer-built" for rate limits and for the ToS "outside your organisation" clauses.
- Use of partner-organisation messages and the "directly benefit a different organisation" clause; partner consent or notice.
- GDPR controller/processor roles for Slack, Telegram and Gmail content; Telegram Business third-party-API restriction; Google "permanent copies" note.

## 10. Sandbox test plan (needs two free Slack workspaces plus a Telegram test bot)
1. Create workspaces A and B (free). Install Ava (custom app, Events API) in A only. Create a Slack Connect channel A-B with B's users. Record the event payloads for: B user posts (check `team_id`, `is_ext_shared_channel`, `context_team_id`, user `team_id`, `authorizations`).
2. Call users.info for a B user from A's token; record fields present/withheld (name, email, `is_stranger`, `team_id`). Call conversations.info and conversations.members; record `shared_team_ids`, `conversation_host_id`, `is_ext_shared`.
3. Edit and delete a message by the B user, and by an A admin; confirm `message_changed` and `message_deleted` reach Ava with correct `deleted_ts`; also test a thread reply deletion, a message with a file, and `file_deleted`.
4. In workspace B (Pro trial or Business+ if available) set a short message retention; check whether A's app gets any `message_deleted` event when B's retention (or A's) removes messages; check whether history still returns them to A.
5. Unshare the channel from the B side and from the A side; check `channel_unshared`, `channel_id_changed`, the new channel ID, and whether events keep arriving.
6. Uninstall the app in A and check ordering of `app_uninstalled` vs `tokens_revoked` and which team_id arrives; reinstall and check whether any history is replayed.
7. Rate-limit check: create one internal custom app and one app marked for distribution (not Marketplace; do not charge) and call conversations.history repeatedly on a new install; record `Retry-After`, the `limit` cap (1000 vs 15). This shows which bucket a self-installed app falls in.
8. Event-loss behaviour: stop the endpoint for three hours with Delayed Events off and on; record what is lost; check whether history can repair it at your tier.
9. Check B's admin view: whether B's admin can see or restrict Ava in the shared channel, and whether B's members see Ava as a member.
10. Telegram: create a group, add the bot with privacy mode on and off, record what arrives; test `message_thread_id` in a forum supergroup and in private chats with topics; edit and delete messages and confirm only `edited_message` arrives (no delete event for normal bots); connect the bot via Telegram Business on a Premium test account and record `business_connection`, `edited_business_message`, `deleted_business_messages`.
11. Ask Slack (feedback@slack.com or Partner contact) in writing, quoting the exact clauses in section 9; keep the reply for the privacy policy file.
