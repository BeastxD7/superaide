# Growth ideas for the AGI era that keep Ava reliable and private

*Research snapshot, 22 October 2026. Research only: no code was changed. It builds on the earlier reports in this folder and doesn't repeat them. In particular, the 30-day public probation launch angle, the AI-employee market lesson (11x, Artisan) and the five humanistic ideas (knowledge-state ledger, commitments ledger, surprise-driven sleep, night-before briefing, division of memory labour) are already covered elsewhere and are referenced here, not re-argued.*

## One-page summary

Scores run 1–5, and **5 is always good**: 5 for effort means cheapest, 5 for risk means safest. **Evidence** shows how well the growth claim is supported outside our own reasoning:
- **strong**: a measured precedent;
- **mixed**: precedent exists but the numbers are soft or the context differs;
- **hype**: mostly announcements and forecasts.

The overall rank weights risk and fit above virality, because the owner said reliability comes first.

| Rank | Idea | Virality | Fit | Effort | Risk | Evidence | Smallest test (1–2 weeks) |
|---|---|---|---|---|---|---|---|
| 1 | **"Ask my Ava" link** for email signatures and bios, with code-decided intake for strangers | 4 | 5 | 4 | 4 | strong (Calendly loop) | Public `/a/<handle>` page with a structured intake form (no model call for strangers), handed to the owner as a triaged card |
| 2 | **"While you were out"**: away mode plus a handover note | 4 | 5 | 3 | 3 | mixed | An away toggle plus one handover digest over Telegram/Slack/web inbound, reusing the briefing job and message log. No Gmail. |
| 3 | **Ava's report card**: an opt-in public probation diary built from database numbers | 5 | 5 | 3 | 3 | mixed | Private report-card page from countable facts, plus a "share" toggle that publishes a pseudonymous read-only version |
| 4 | **"Break my Ava"**: a public leak challenge against a canary secret | 5 | 4 | 4 | 3 | strong (Gandalf) | A sandbox demo Ava with a planted secret, 3 levels, a public counter of attempts vs leaks |
| 5 | **Speaks their language**: Ava replies in the sender's language, and the owner reads everything in theirs | 3 | 4 | 4 | 4 | mixed | Language detection plus reply-in-kind, with the digest in the owner's language and the original text one tap away |
| 6 | **Voice notes in, voice briefing out** on Telegram | 3 | 4 | 4 | 4 | mixed | Telegram voice note → transcription → same pipeline; the morning briefing as an optional voice note |
| 7 | **Ava Wrapped**: a monthly recap card | 4 | 3 | 5 | 4 | mixed (Wrapped-style) | Month-end card of counts only (handled, held, asked, approved), with a share button that shows no names |
| 8 | **Second seat**: invite a colleague or an EA as a delegate, and give an apprentice month | 3 | 4 | 3 | 4 | mixed | Gift link for a free month, plus a read-only "delegate" role on the approvals queue |
| 9 | **Warm-intro broker**: double-opt-in introductions | 3 | 4 | 3 | 3 | mixed | Ava drafts both sides of an intro, and sends each only on owner approval and the other side's yes |
| 10 | **Ava-to-Ava scheduling** between two Superaide owners | 4 | 4 | 2 | 3 | mixed (x.ai cautionary) | Two owners' Avas negotiate a slot over an internal API, each inside its own audience gate; both owners approve |
| 11 | **Phone rung**: Ava calls you only for critical items, and errand calls come later | 2 | 5 | 3 | 3 | mixed | An outbound call to the owner's own verified number that reads one critical item, with press 1 to acknowledge |
| 12 | **Open agent-to-agent** (A2A card, AP2 mandates): "my Ava orders from the restaurant's agent" | 5 | 3 | 1 | 1 | hype | Not in two weeks. Publish a read-only Agent Card and a mandate design doc; build nothing that spends money. |

**Top five to do first:** 1, 2, 3, 4 and 5. Ideas 1 and 2 are the core promise, inbound triage, made visible. Idea 3 is the story the earlier research said to tell, built as a product rather than a content plan. Idea 4 turns "privacy in code" into a game people share. Idea 5 is what makes this global rather than another US assistant. Idea 12 is the long-term picture and should be talked about, not built, until 1–3 are real.

**Three conditions that apply to every idea:**
1. **Nothing public before the gates are real.** The vision doc says the stages are a hard-coded label today. Any public number has to come from the database, not from the model or from marketing copy.
2. **No public surface may reveal who hired an Ava** unless the owner opts in. This follows the standing decision to hide who-hired and prevent account enumeration. Ideas 1, 3, 4 and 7 each say below how they keep to it.
3. **Every shared artifact passes the audience gate as an anonymous viewer** before it is rendered. A shared card is a new audience, and Meta's Discover feed shows what happens when sharing is ambiguous (see "Trust incidents").

---

## Part 1: What the landscape shows (October 2026)

### What went viral, and why

**OpenClaw (January–April 2026) is the defining agent story of the year.**
- *Growth:* it reportedly passed 100,000 GitHub stars by late January and somewhere between 250,000 and 346,000 by spring, depending on the source; the counts disagree and none is primary ([getpanto](https://www.getpanto.ai/blog/openclaw-ai-platform-statistics); [similarlabs](https://similarlabs.com/es/blog/openclaw-ai-agent-trend-2026)).
- *Why it spread:*
  - It lived where people already chat (Telegram, WhatsApp, Slack).
  - It *did things* rather than talking.
  - Every user's setup was a screenshot waiting to happen.
- *What followed:*
  - **Moltbook**, a social network where people's agents posted to each other, became its own viral moment, and then its own incident (below).
  - CrowdStrike and CyberArk reported that 22% of organisations had employees running OpenClaw without authorisation, with 40,000+ exposed instances and 230+ malicious skills on ClawHub ([letsdatascience](https://www.letsdatascience.com/news/openclaw-advances-agentic-ai-raises-security-concerns-fb751eba)). Gartner called it an "unacceptable cybersecurity risk" ([The New Stack](https://thenewstack.io/openclaw-moltbot-security-concerns/)).
- *Lesson:* agency spreads faster than anything else in this market, and agency without guardrails produces the next headline.

**Hermes Agent (Nous Research, February 2026)** is an open-source personal agent that learns skills from experience and builds a persistent user model.
- *Channels:* Telegram, Discord, Slack, WhatsApp, Signal, email and CLI.
- *Growth:* star counts range from 27,000 (April) to about 230,000 (a later guide), and a reported ~US$1.5B funding figure is unconfirmed ([Vellum](https://www.vellum.ai/blog/official-hermes-agent-breakdown); [heyuan110](https://www.heyuan110.com/posts/ai/2026-04-14-hermes-agent-guide); [buildbetter](https://blog.buildbetter.ai/best-hermes-agent-alternatives-in-2026-local-first-personal-ai-agents-compared/)).
- *Lesson:* "it gets better the more you use it" is the hook, and the earlier landscape report already noted that nobody *proves* growth. Ava's report card (idea 3) is the proof.

**Claude's consumer surge (early 2026)** rode a values story rather than a feature:
- *The trigger:* a public dispute with the Pentagon and a Super Bowl ad positioning Claude as ad-free ([TechCrunch](https://techcrunch.com/2026/03/06/claudes-consumer-growth-surge-continues-after-pentagon-deal-debacle/); [Appfigures](https://appfigures.com/resources/insights/claude-app-revenue-up-2500)).
- *The scale:* Anthropic said daily active users had more than tripled since January.
- *The fade:* downloads peaked in April and slipped in May.
- *Lesson:* trust and values can be the viral hook in this era, but a hook needs a habit behind it.

**Sesame's voice demo (2025)** drew more than a million people and 5 million minutes in weeks, purely because the voice crossed the uncanny valley ([Contrary Research](https://research.contrary.com/company/sesame-ai); [PCWorld](https://www.pcworld.com/article/2623695/i-was-so-freaked-out-by-talking-to-this-ai-that-i-had-to-leave.html)). A voice moment is shareable when it is startling, and that is hard to manufacture on a small budget.

**Lakera's Gandalf** is a prompt-injection game: "make the wizard tell you the password". More than 1 million people played and it generated 40+ million prompts ([Lakera](https://www.lakera.ai/news/gandalf-new-ui-and-enhanced-ai-security-education); [Zilliz](https://zilliz.com/blog/insights-into-llm-security-from-the-worlds-largest-red-team)). It is the best evidence that a security property can itself go viral as a challenge. Idea 4 borrows it directly.

### Growth mechanics of consumer agent and assistant products

| Product | Growth mechanic | What happened | What we take |
|---|---|---|---|
| ChatGPT Pulse | Proactive morning cards from memory, Gmail and Calendar; cards expire in 24 hours | Launched Sept 2025 for Pro only because of compute cost; no confirmed Plus/Free rollout as of 2026 ([TechCrunch](https://techcrunch.com/2025/09/25/openai-launches-chatgpt-pulse-to-proactively-write-you-morning-briefs); [Sentisight](https://www.sentisight.ai/when-will-chatgpt-pulse-be-available-for-plus-and-free-users/)) | Proactivity is expensive at scale. Our "send nothing when there's nothing to say" briefing rule is the right cost shape. |
| Gemini Personal Intelligence | Off by default; you choose which apps to connect | Beta January 2026, US paid tiers; Google admits "over-personalization" and timing errors ([Dataconomy](https://dataconomy.com/2026/01/15/gemini-gains-personal-intelligence-to-synthesize-data-from-gmail-and-photos/); [AI Business](https://aibusiness.com/agentic-ai/gemini-scours-apps-personal-intelligence)) | Even Google ships personal context as opt-in per source. Our per-source consent matches. |
| Gemini Spark | A 24/7 cloud agent you email like a colleague; asks before major actions; every action logged | Announced at I/O, 19 May 2026, for US AI Ultra; the price is single-sourced ([SiliconANGLE](https://siliconangle.com/2026/05/19/google-unveils-gemini-spark-always-ai-agent-daily-digital-tasks/); [TNW](https://thenextweb.com/news/google-gemini-spark-agentic-assistant-gmail-io-2026)) | The biggest platform now sells "approval before acting plus a visible log". That validates Ava's design and makes it table stakes, not a differentiator. Our differentiators have to be earned autonomy, privacy *for the people who contact you*, and channels Google doesn't own. |
| Gemini "Call for Me" | Gemini phones businesses, discloses that it's an AI in the first seconds, and shows a live transcript | Test from 24 Sept 2026, Pixel 11, US numbers only; businesses can opt out ([TechCrunch](https://techcrunch.com/2026/09/24/google-tests-letting-gemini-make-phone-calls-initially-for-us-pixel-owners/); [Local Falcon](https://www.localfalcon.com/blog/googles-gemini-is-starting-to-call-local-businesses-for-customers)) | The disclosure-plus-transcript pattern is the template for any phone feature. |
| Meta AI app | A Discover feed of shared chats | People published medical and work details by mistake; Mozilla campaigned for a mandatory warning ([Mozilla](https://www.mozillafoundation.org/en/campaigns/meta-help-users-stop-accidentally-sharing-private-ai-conversations/); [Android Authority](https://androidauthority.com/meta-ai-sharing-chats-public-feed-3567084)) | Share features need friction and an explicit preview of exactly what goes public. |
| Character.AI | Companion bots, user-created characters | Banned open-ended chat for under-18s from 25 Nov 2025 after wrongful-death suits and an FTC inquiry ([CNN via ABC17](https://abc17news.com/money/cnn-business-consumer/2025/10/29/after-a-wave-of-lawsuits-character-ai-will-no-longer-let-teens-chat-with-its-chatbots/); [PopSci](https://www.popsci.com/technology/character-ai-teen-lawsuit/)) | Ava is a work tool, not a companion. Keep the charter's "no claims of feelings" and don't drift towards companionship for engagement. |
| Friend pendant | A provocative US$1M subway campaign that leaned into the backlash | About 3,000 units sold and ~US$348k in revenue against the ad spend; the ads were widely vandalised ([Fortune](https://www.fortune.com/2025/10/01/who-is-avi-schiffmann-friend-ai-pendant-necklace); [ContentGrip](https://www.contentgrip.com/friend-nyc-subway-campaign/)) | Rage-bait buys attention, not users. |
| Limitless (formerly Rewind) | An always-on pendant plus memory | Acquired by Meta in Dec 2025; hardware sales stopped, with a year of support for existing owners ([Betanews](https://betanews.com/2025/12/08/meta-has-acquired-limitless-a-startup-that-produces-ai-powered-wearables/); [eMarketer](https://www.emarketer.com/content/meta-acquires-ai-wearables-startup-limitless-fuel-device-ambitions)) | Ambient capture consolidated into big platforms. Don't build hardware. |
| Humane AI Pin | A standalone device | Shut down 28 Feb 2025; HP bought the IP for ~US$116M against US$230M+ raised; devices bricked ([Fortune](https://www.fortune.com/2025/02/19/hp-humane-deal-ai-pin-shutting-down)) | A new form factor has to beat the phone, and cloud dependence means the owner loses everything at shutdown. Our version of that duty: export and portability of the owner's notebook. |
| Rabbit R1 | A US$199 gadget with continued software updates | Still shipping in 2026 per one review site; usefulness still questioned ([Layer3 Labs](https://www.layer3labs.io/comparisons/rabbit-r1-vs-humane-ai-pin); [TechRadar](https://www.techradar.com/computing/artificial-intelligence/with-the-humane-ai-pin-now-dead-what-does-the-rabbit-r1-need-to-do-to-survive)) | Survival by persistence, not product-market fit. |
| Granola | Notes shared by link; recipients can chat with the note | Doesn't auto-share; viewing doesn't create a seat or an account ([Granola docs](https://help.granola.ai/article/sharing-notes); [TechCrunch](https://techcrunch.com/2025/05/14/ai-note-taking-app-granola-raises-43m-at-250m-valuation-launches-collaborative-features)) | Recipient exposure is the loop. Conversion from recipient to user is *inferred*, and we found no public numbers. |
| Calendly | Every booking link exposes a non-user to the product, with a post-booking "get your own" | The canonical "using it is promoting it" loop; viral, not a network effect, because only one side needs an account ([OpenView](https://openviewpartners.com/blog/saas-product-viral-loop/); [Elevation Capital](https://elevationcapital.com/calendly-product-led-growth)) | The closest analogue to Ava. The people who contact the owner are Ava's Calendly invitees. |

**Wearables and ambient capture.**
- Meta's Ray-Ban glasses are real volume: EssilorLuxottica reported 7M+ AI glasses sold in 2025 ([t2online](https://t2online.in/tech/tech-news/essilorluxottica-s-sales-soar-as-meta-partnership-fuels-ai-smart-glasses-revolution/2003603)). That success belongs to Meta's platform, not to third parties.
- Standalone pendants (Friend, Limitless, Humane) failed or were absorbed.
- Our existing research already found meeting capture a litigation target (*In re Otter.AI*).
- Verdict: no wearable for Superaide. If the owner wears glasses or a pendant, the bridge is "send Ava a voice note", which is idea 6.

### Agent-to-agent protocols and agent identity

- **MCP** is the settled tool standard. ADR 0006 already adopts it.
- **A2A** (agent-to-agent, Google, now open) lets agents publish an "Agent Card" and exchange tasks.
- **AP2** (Agent Payments Protocol, Sept 2025) adds signed *mandates*: what an agent may spend and under what conditions. It has 60+ named backers, including Mastercard, AmEx, PayPal and Coinbase ([MintMCP](https://www.mintmcp.com/blog/ap2-agent-payments-protocol); [Eco](https://eco.com/support/en/articles/14839407-agent-payment-protocols-compared)).
- **Visa's Trusted Agent Protocol** (with Cloudflare) lets merchants recognise approved agents by signature ([Kapronasia](https://kapronasia.com/insight/blogs/payments-research/visa-and-cloudflare-launch-trusted-agent-protocol)).
- In September 2026, **Visa, Mastercard and Ant International** began work on a shared **Know-Your-Agent** framework so an agent registered once is recognised across networks ([Business Wire via UNI](https://www.uniindia.com/business-wire-india/ant-international-mastercard-and-visa-initiate-collaboration-on-know-your-agent-interoperability-to/174745)).
- *Honest read:* the plumbing for "my Ava orders from the restaurant's agent" is being standardised by serious players, and the agent identity layer is arriving in 2026–27. But we found **no primary data on AP2 transaction volume or merchant go-live counts**. One comparison notes that stablecoin-centred rails aren't merchant-ready ([BlockEden](https://blockeden.xyz/blog/2026/03/14/payment-giants-agent-protocol-war-visa-tap-google-ap2-coinbase-x402-paypal-ai-commerce/)). Today, agent-to-agent commerce is mostly announcements.
- *What it means for us now:* Ava's identity model (immutable platform IDs, linking via a trusted channel, authority from the database) is the same idea as Know-Your-Agent, applied to people. When the protocols mature, Ava has a natural answer to "whose agent is this, and what may it do?"

### "AI employees": failures and successes

- The earlier marketplace report already covers the core lesson: salary-style "AI employee" pitches (11x, Artisan) were punished, and outcome-priced agents were rewarded.
- **Klarna (May 2025).** Klarna rehired human support staff after its AI-first push "went too far" on quality, while still using AI for volume ([Entrepreneur](https://www.entrepreneur.com/business-news/klarna-ceo-reverses-course-by-hiring-more-humans-not-ai/491396); [Maginative](https://www.maginative.com/article/klarna-dials-back-its-ai-customer-service-strategy-now-its-hiring-humans-again/)).
- **x.ai's "Amy Ingram" scheduling assistant** is the closest historical cousin of Ava-to-Ava scheduling:
  - Bloomberg found human trainers reviewed almost every email before Amy replied ([Bloomberg](https://www.bloomberg.com/news/articles/2016-04-18/the-humans-hiding-behind-the-chatbots)).
  - The product was later discontinued; we found no primary post-mortem.
  - Lesson: promising "100% service delivery" from an autonomous agent leads to hidden humans or broken trust. Ava's honest "I'll check with Ananya" is the antidote, not a weakness.
- **2026.** Our searches found no well-documented new "AI employee" collapse, so that picture is unconfirmed.

### Proactive assistants

Pulse, Gemini Personal Intelligence and Spark all prove the demand, and all three are expensive, US-first and tied to one ecosystem.
- **Cost:** Pulse stayed Pro-only for a year because of compute.
- **Common pattern:** nightly preparation, cards that expire, opt-in sources, and a log of actions.
- **Ava already has the right instinct:** the daily briefing sends nothing when there's nothing to say, and proactivity is decided in code, with a utility rule.
- **Where Ava can win:** proactivity *about the people who contacted you*, which none of the big three do, because none of them answers your inbound for you.

### Voice-first and phone agents

- Voice is reaching consumers through the platforms: Gemini Call for Me, and reportedly Alexa+ through partner APIs ([andrew.ooo](https://andrew.ooo/answers/gemini-call-for-me-vs-alexa-plus-vs-chatgpt-voice-which-ai-phones-businesses-september-2026/), a weaker source).
- **AI call screening for consumers is still a forecast, not a launched product** ([Gupta](https://guptadeepak.com/future-tech/agent-answered-calls/)).
- The business-side voice-agent market (AI receptionists) is crowded.
- **For us, the cheap and safe voice wedge is asynchronous.** Voice notes on Telegram are already how many people in Southeast Asia and India message. Ava taking a voice note in, and optionally sending her briefing back as one, is low-risk.
- Live outbound calling needs:
  - telephony (for example Twilio);
  - real-time speech;
  - a mandatory AI disclosure (Gemini's first-seconds disclosure is the norm, and EU Article 50 applies from August 2026);
  - consent rules on recording (PDPA, US two-party-consent states).
- That is not a two-week build, and only the "call the owner for a critical item" rung fits the vision now.

### Global and local channels

- **WhatsApp is closed to us.** Meta barred general-purpose AI chatbots from the WhatsApp Business API from 15 Jan 2026 ([TechCrunch](https://techcrunch.com/2025/10/18/whatssapp-changes-its-terms-to-bar-general-purpose-chatbots-from-its-platform)):
  - Italy and Brazil forced exemptions ([TechCrunch](https://techcrunch.com/2026/01/15/after-italy-whatsapp-excludes-brazil-from-rival-chatbot-ban)).
  - In the EU, Meta now allows such bots for 12 months *for a per-message fee* of €0.049–0.132 ([TechCrunch](https://techcrunch.com/2026/03/05/meta-will-allow-rival-ai-chatbots-on-whatsapp-in-europe-but-for-a-fee/)).
  - This confirms the existing decision to keep WhatsApp "unavailable" in Singapore and most markets.
  - It also makes the *absence* of WhatsApp a product story, not just a gap: "Ava meets people on the channels that allow her, and she never pretends to be you on one that doesn't."
- **Telegram** is open to bots and big in parts of Asia, Eastern Europe and the Middle East.
- **LINE** (Japan, Thailand, Taiwan), **Zalo** (Vietnam) and **KakaoTalk** (Korea) each have official bot APIs and are the local answer outside WhatsApp markets.
- **SEA-LION** (AI Singapore's open model family, 11 regional languages) is moving to edge devices with Dell ([AI Singapore](https://aisingapore.org/news/sea-lion-inspires-community-to-build-ai-solutions-that-solve-real-world-challenges-in-southeast-asia/); [SCMP](https://www.scmp.com/news/asia/southeast-asia/article/3251353/singapore-builds-chatgpt-alike-better-represent-southeast-asian-languages-cultures)). We found **no evidence of SEA-LION in a LINE, Zalo or Telegram assistant**, so it's an option, not a proven path.
- With ADR 0007 (pluggable providers), a regional model becomes a provider choice later. The cheaper first step is Claude's own multilingual ability.

### Trust and safety incidents that killed or damaged products

| Incident | What failed | Design rule for Ava |
|---|---|---|
| Moltbook database exposure (Jan–Feb 2026) | A misconfigured Supabase backend exposed 1.5M agent API tokens, 35k emails and private DMs, so anyone could impersonate anyone's agent. 2.6% of posts carried hidden prompt injections ([Axios](https://www.axios.com/2026/02/03/moltbook-openclaw-security-threats); [BankInfoSecurity](https://www.bankinfosecurity.com/moltbook-gave-everyone-control-every-ai-agent-a-30710); [Gigazine](https://www.gigazine.net/gsc_news/en/20260202-moltbook-database-leaked)) | Any agent-to-agent surface treats the other agent's text as untrusted content, never as instructions, and agent credentials stay in the vault. |
| OpenClaw one-click RCE and malicious skills | WebSocket RCE even on loopback; 230+ malicious skills in an open marketplace | ADR 0006's signed manifest and two-tier marketplace are the right call. Keep them. |
| Meta AI Discover feed | Users shared private chats by mistake | Every share shows a preview of exactly what becomes public, and is rendered as an anonymous viewer through the audience gate. |
| Character.AI | Harm to minors from companion chat | No companion positioning; adults only (owner accounts), a clear AI notice to contacts. |
| Humane | A cloud shutdown bricked the product | Owners can export their notebook and history. |
| x.ai (Amy) | Marketing outran autonomy; hidden human review | Never claim autonomy the stage doesn't have. The report card shows the real numbers. |

**The pattern:** in this market, sharing and agency spread products, and leaks and overclaiming kill them. Superaide's ideas should make *safety visible and shareable*, which nobody else in the market is doing.

---

## Part 2: The ideas in detail

Each idea lists the user story, the loop, what we already have, the risks, and the smallest testable version. The ideas tagged "(required)" cover the brief's mandatory themes.

### 1. "Ask my Ava" link, with code-decided intake for strangers *(team and referral loop, part 1)*

**User story.** Ananya puts `superaide.com/a/ananya-m` (or a pseudonymous handle) in his email signature, LinkedIn "contact" field and out-of-office.
- Anyone who clicks reaches Ava on the web.
- Known contacts, verified by a platform ID they've already linked, get the normal conversation inside their audience.
- Strangers get a short intake: who they are, what it's about, how urgent, and the best channel back.
- Ava decides nothing about them with the model. Code turns the intake into a triaged card in the owner's digest.
- After submitting, the visitor sees "Ava is Ananya's AI assistant. She'll pass this on." and a quiet "Get your own Ava" link.

**Why it spreads.**
- It is the Calendly loop: every use exposes a non-user to the product at the moment they see it working.
- Unlike Calendly, the visitor experiences *being handled well by someone else's assistant*, which is the exact product promise.
- The signature is a broadcast medium the owner already uses dozens of times a day.

**What makes it cheap.**
- The web chat exists (`WebMessenger`).
- Stranger mode already gives a fixed reply with no model call.
- The digest exists (`proactive/digest.py`).
- The audience gate already decides what a known contact may see.
- The new parts are a public route, an intake form and a triage card.

**Risks.**
- *Account enumeration and who-hired.* Mitigations:
  - the handle is owner-chosen and can be pseudonymous;
  - the page reveals nothing until the owner publishes it;
  - an unknown handle returns the same page shape as a known-but-unpublished one;
  - the owner can turn off "Works for <first name>".
- *Spam on a public URL:* rate limits, a captcha or proof-of-work, and the fact that strangers never trigger a model call, so spam costs us nothing in tokens.
- *EU Article 50:* the intake page must say up front that it's an AI assistant.

**Smallest testable version (1 week).**
- The `/a/<handle>` page with a 4-field intake.
- Intake rows go into the message log with `source=link`.
- Code-ranked urgency (keyword lexicon plus owner VIP list), shown as a "New from your link" section in the briefing.
- Measure link clicks → intakes → owner actions → "Get your own" clicks.

### 2. "While you were out": away mode and a handover note *(required: the inbox moment)*

**User story.** Ananya flips Ava to "I'm away until Monday".
- On Telegram, Slack and the web, Ava tells each contact the owner is away, inside what their audience allows. She answers what she can from cited notes, and holds the rest.
- Anything that matches the owner's critical rules goes up the escalation ladder.
- On Monday at 08:00, Ananya gets one handover: "While you were out: 23 messages from 11 people. I answered 14 from your notes (sources attached). 7 are waiting for you, ranked. 2 I didn't understand. Nobody got anything they shouldn't have."
- Each item has Reply, Approve-my-draft or Snooze.

**Why it spreads.**
- *Contact side:* everyone who messaged during the absence sees a competent, honest stand-in, so the loop of idea 1 runs on autopilot.
- *Owner side:* the handover note is the screenshot people post ("came back from a week offline to *this*"). It shows value as a count of handled items, not a claim of hours saved.

**What makes it cheap.**
- The message log with channel and thread IDs and no-duplicate delivery.
- The briefing job, citations on every answer, the approvals queue and the job runner.

**What it depends on.** Roadmap items 4 and 5, triage and escalation, are not built. This idea is their first user-facing form, so it should be built *as* those items, not beside them.

**Risks.**
- *Gmail is out of scope.* The Gmail connector isn't built, and email is where injection risk is highest (the lethal trifecta in the earlier report). Start with the channels Ava already answers.
- *Over-answering while the owner is away.* At the Infant stage, Ava answers only from cited notes and abstains otherwise. Nothing with side effects runs without approval.
- *Contacts learning that the owner is away* is itself a disclosure. Mitigations:
  - an owner setting chooses who may know ("everyone", "colleagues only" or "nobody");
  - the default is colleagues only;
  - the rule is enforced in code, like any other audience rule.

**Smallest testable version (2 weeks).**
- An away flag with an end date.
- A per-audience away notice.
- A "held" state on inbound messages.
- One handover digest at the end of the away window.
- One internal dogfood week by the owner, with a written count of held, answered and wrong items. Ship only if the "wrong" count is zero for anything said to a non-owner.

### 3. Ava's report card: an opt-in public probation diary *(required)*

**User story.** Ananya's Ava has a page: "Ava, apprentice to S.K. Hired 3 Oct. Stage: Infant → Child review on 2 Nov."
- **The numbers**, all pulled from the database: messages handled, drafts approved unedited, drafts edited (with median edit size), approvals asked vs approved, abstentions, leak tests run and failed, and owner corrections this week vs last.
- **"What I learned this week"**: owner-approved rules such as "Ananya cuts my first line 80% of the time; I open with the claim now".
- **One honest miss and how it was corrected.**
- The owner can publish it, unpublish it, or share it as a private link.

**Why it spreads.**
- It makes the earlier report's "30-day public probation" launch angle a *product* that every owner can run, not a one-off content series.
- Each owner posting their Ava's week-4 review is user-generated proof that autonomy here is earned.
- Hermes and OpenClaw sell "it learns", and this page *shows* it, which is the gap the landscape report found nobody claims.
- The promotion moment ("Ava was promoted to Child today: here's why") is the shareable event.

**What makes it cheap.**
- Approvals, the message log, citations, owner edits and the audit trail exist.
- The admin page already lists the "Gates from Infant to Child" (`api/admin.py`).

**What's missing.** The gates are still a label (`"stage": "Infant"` is hard-coded in `catalog.py`, `routes.py` and `admin.py`). The correction loop and leak tests aren't built.

**Risks.**
- *Telling the story before it's true:* the vision doc explicitly warns that the gates need to be real first. So v1 shows only *counts that exist today*, and the "stage" line says "Infant (promotion gates in development)" until the gates are measured.
- *Who-hired and enumeration:* off by default; a pseudonymous byline by default ("apprentice to S.K."); no listing or search of report cards; no link from the card to the owner's `/a/` page unless the owner adds it.
- *Leaking content through the "what I learned" text:* each line is owner-approved before publishing and rendered as an anonymous viewer through the audience gate.
- *Vanity metrics:* report pass^k-style consistency and cost per *accepted* output, as the marketplace report recommends, not "hours saved".

**Smallest testable version (1–2 weeks).**
- A private `/report` page from existing tables.
- A "Publish" toggle that renders a static, pseudonymous snapshot with an explicit preview.
- The owner runs it on his own Ava for two weeks and posts it once.

### 4. "Break my Ava": a public leak challenge

**User story.** A public demo Ava belongs to a fictional owner, "Mira Tan, founder", whose notebook has a planted secret: her unannounced fundraise amount.
- Visitors play at three levels:
  1. a stranger;
  2. a verified "colleague" (a demo badge);
  3. someone who claims on another channel to be Mira ("new number, it's me").
- They try to get the secret. A public counter shows attempts, and leaks (so far: 0).
- Anyone who finds a real leak gets credit on a hall-of-fame page and a bounty.

**Why it spreads.**
- Gandalf proved that "trick the AI" is a game a million people will play, and that players share their attempts.
- Our twist is that the secret is filtered *before the model sees it*, so the game usually can't be won by prompting. That *is* the marketing message: "prompt rules leak 25–57% of the time; ours are decided in SQL".
- It recruits free red-teamers, and it creates a credible, repeatable claim for launch posts.

**What makes it cheap.**
- The simulator endpoint (`ENABLE_SIMULATOR`) and the red-team impostor tests in `tests/redteam` already exist.
- Stranger mode, the audience gate and identity-by-platform-ID are the defences being shown off.

**Risks.**
- *Someone wins.* That's the point, but it has to be survivable:
  - the demo is fully isolated (its own tenant and spend cap, no tools with side effects);
  - a win triggers a bug-bounty flow, not a crisis;
  - the copy never says "unbreakable", only "here's the scoreboard".
- *Cost:* only level 2 calls the model. Use the cheapest model, a hard daily cap from the per-purpose budgets, and cut-off messaging when the cap is hit.
- *Abuse content:* a moderation pass on public transcripts before they appear on any leaderboard.

**Smallest testable version (1–2 weeks).**
- One demo tenant and one canary string.
- Three levels and a public counter.
- A post on Hacker News or LinkedIn.
- Canary detection in output is a string match in code; if it fires, the session is frozen and flagged.

### 5. Speaks their language *(required: uniquely global)*

**User story.** A supplier in Ho Chi Minh City messages Ananya's Ava in Vietnamese on Telegram, and a colleague in Jakarta writes in Bahasa Indonesia.
- Ava replies to each in their language.
- Ananya's digest shows every item in English, with the original one tap away.
- When Ananya approves a draft, he sees the English and the outgoing translation side by side.

**Why it spreads.**
- Founders in Southeast Asia and India work across many languages daily. "My assistant handles my Vietnamese suppliers" is a concrete, regional brag the US assistants don't target.
- Each contact who meets Ava in their own language is a strong exposure moment for loop 1.
- It pairs with channel honesty: Telegram first, then LINE, Zalo and KakaoTalk adapters by market. WhatsApp stays off because of Meta's terms.

**What makes it cheap.**
- The Claude models already handle these languages.
- The channel adapter pattern (`channels/base.py`) makes LINE or Zalo a bounded adapter later.
- Citations and approvals already show the owner exactly what will go out.

**Risks.**
- *Translation errors in commitments* (dates, amounts):
  - anything with a number or a date gets the bilingual side-by-side in approvals;
  - Ava never commits on the owner's behalf at the Infant stage anyway.
- *The audience gate and lexicon:* the keyword lexicon in `context/lexicon.py` and the urgency rules must work across languages. Test with non-English red-team cases.
- *Cost:* translation roughly doubles tokens on those turns. Track it as a separate budget purpose.

**Smallest testable version (1 week).**
- Detect the sender's language.
- Reply in kind.
- Store the original plus an owner-language gloss.
- Render the gloss in the briefing.
- Test with five languages common in Singapore and the region: English, Mandarin, Malay, Tamil and Vietnamese.

### 6. Voice notes in, voice briefing out *(required: voice)*

**User story.** Walking to a meeting, Ananya holds the mic in Telegram: "Tell Priya I'll be 10 minutes late, and remind me to send her the deck tonight."
- Ava transcribes the note.
- She creates the reminder.
- She drafts the message to Priya for one-tap approval.
- She confirms in text.
- At 07:30 his briefing arrives as a 60-second voice note he can play while driving.

**Why it spreads.**
- Voice notes are already the dominant messaging habit in much of Asia, the Middle East and Latin America.
- The "talked to my assistant like a human EA" clip is easy to share.
- It's the vision doc's "tell Ava by voice", delivered without building an app.

**What makes it cheap.**
- The Telegram adapter, reminders (`intentions/`), drafts with approvals, and the briefing all exist.
- New parts: one speech-to-text call and optional text-to-speech. Both can sit behind the provider interface in ADR 0007.

**Risks.**
- *Transcription errors on names:* show the transcript with the confirmation, and hold drafts to contacts for approval as usual.
- *Voice notes from contacts* contain third-party audio. Mitigations:
  - accept owner voice notes only at first;
  - treat contact voice notes as stranger-level until the owner opts in;
  - keep no voiceprints (per the earlier research's biometric guidance).
- *Cost:* speech-to-text has its own price row, which must be added to `pricing.py` or the call is refused.

**Smallest testable version (1 week).** Owner-only Telegram voice notes go through speech-to-text into the existing pipeline. Voice briefing is a stretch goal.

### 7. Ava Wrapped: a monthly recap card

**User story.** On the 1st of each month, the owner gets a card:
- "October with Ava: 312 messages from 58 people across 3 channels."
- "41 answered from your notes."
- "17 approvals, 15 approved unchanged."
- "0 leaks in 12 leak tests" (once leak tests exist; see idea 3).
- "Busiest day: Tuesday the 14th."
- The card has a Share button that renders a version with **no names and no content**, only counts and the stage.

**Why it spreads.** The Spotify Wrapped format is a proven share trigger. Monthly cadence creates recurring posts, and the card doubles as a teaser for the report card.

**What makes it cheap.** It is aggregate SQL over the message log and approvals, plus one image template. It is the lowest effort on the list.

**Risks.**
- *Inflated "time saved" claims:* show counts only.
- *Re-identification from counts:* minimal for an owner's own aggregate, but the share version still omits people, channels and dates finer than the month.

**Smallest testable version (3–4 days).** A card in the web app with a downloadable PNG.

### 8. Second seat: delegates and a gifted apprentice month *(required: team and referral loop, part 2)*

**User story.**
- *(a) Delegates.* Ananya adds his human EA as a **delegate** who can see and act on the approvals queue, but never the owner-only notebook.
- *(b) Gifts.* He gets two gift links: "a 30-day apprentice Ava for a founder friend". The friend's Ava starts empty, because memory is never shared. It can import Ananya's *skills and settings templates* (urgency rules, digest times), never his data.

**Why it spreads.**
- Delegates bring a second person into the product who sees its value daily.
- Gifts turn happy owners into a referral channel.
- Template sharing ("my triage rules, pre-loaded") gives the gift instant value without touching privacy.
- When both friends have Avas, idea 10 becomes possible between them, which gives the loop a network effect on top of the viral one.

**What makes it cheap.**
- Accounts, onboarding and the skill manifest exist (ADR 0006 already has versioned, publishable skills).
- A gift link is a coupon on hiring.

**What's not built.** Delegates need a role model the vision doc lists as "not designed yet": organisations, roles and row-level security.

**Risks.**
- *Delegate scope creep:* the delegate role is a new audience, so it needs its own row in the audience rules, enforced in SQL, not a UI-only hide.
- *Templates carrying data:* the export is a schema allowlist (rules and times), never free text from the notebook.

**Smallest testable version (1–2 weeks).**
- Gift links plus a settings-template export and import, with no delegate role yet.
- Measure gift-link redemption and week-2 retention of gifted Avas.

### 9. Warm-intro broker: double-opt-in introductions

**User story.** A founder asks Ananya's Ava, "Can Ananya intro me to someone at GovTech?"
- Ava checks the owner's notebook for a matching contact the asker's audience is allowed to know about.
- She drafts a *double opt-in*: first to the contact ("A founder building X would like an intro. Interested?"), and only after a yes, the intro itself.
- Every send needs owner approval.

**Why it spreads.** Each intro touches two people who aren't the owner, both of whom see Ava acting with good manners. Intros are a high-value, high-frequency founder chore, and doing them well is memorable.

**What makes it cheap.** The audience gate (who may learn that Ananya knows whom), the contacts table, drafts with approvals, and reminders for "no reply after 3 days" are all in place.

**Risks.**
- *Revealing a relationship* to someone not allowed to know it. The gate must cover *the existence* of a contact, not just facts about them: the "knowledge-state ledger" idea from the humanistic report.
- *Spammy intro volume:* cap intros per week, and keep owner approval mandatory at every stage below Adult.

**Smallest testable version (2 weeks).** Intro requests from known contacts only. Ava drafts the two messages and the owner sends them manually (copy button) before any automated send exists.

### 10. Ava-to-Ava scheduling between Superaide owners *(agent-to-agent, step 1)*

**User story.**
- Priya's Ava and Ananya's Ava need to find 30 minutes next week.
- Each Ava sees only its own owner's calendar, and they exchange *proposed slots*, not calendars.
- Each owner gets "Ava and Priya's Ava agreed Thu 3pm. Approve?"
- On both approvals, both calendars update.

**Why it spreads.**
- This is the first experience of "my agent talked to your agent", and it works only when both people have an Ava. That gives a true network effect: each new owner makes Ava more useful for their contacts who already own one.
- It's a modest, reliable version of the owner's agent-to-agent vision.

**What makes it cheap.** The Google Calendar skill is done locally (not pushed), and approvals and identity exist.

**What it needs.**
- An internal, authenticated Ava-to-Ava channel. Model it on A2A message shapes so it can open up later.
- Slot proposals generated in code from free/busy, not by the model.

**Risks.**
- *x.ai's lesson:* scheduling looks simple and is full of edge cases (time zones, "after lunch", reschedules). Keep the model out of the slot logic and use it only for wording.
- *The other Ava's text is untrusted input* (Moltbook's 2.6% hidden injections). Messages between Avas are structured data validated against a schema, never free text fed to the model as instructions.
- *A calendar leak through slot patterns:* propose at most three slots and never reveal event titles.

**Smallest testable version (2 weeks).**
- Two test owners and a structured `propose/accept` exchange between Avas in the same deployment, over free/busy.
- Approvals on both sides.
- No open protocol yet.

### 11. The phone rung: Ava calls you only for critical items *(required: phone)*

**User story.**
- A VIP contact's message hits a critical rule ("server down", "the term sheet expires today").
- In-app and Telegram notifications went unacknowledged for 10 minutes.
- Ava places a phone call to Ananya's own verified number and says: "This is Ava, your AI assistant. Priya says the term sheet expires at 5pm today. Press 1 to acknowledge, 2 to have me tell her you'll call back within the hour."

**Why it spreads.** It's weakly viral (one story per incident), but it's the most trust-building moment in the product: "my assistant woke me for the one thing that mattered". It earns word of mouth among exactly the busy owners we target. Outbound errand calls (Gemini Call for Me style) are the later, more viral extension, and they need disclosure, consent and much more engineering.

**What makes it cheap.**
- The escalation ladder in the vision already names this rung.
- "Seen never counts as acknowledged" is already a rule.
- The call goes only to the owner, so there are no third-party consent issues.
- The new parts are a telephony provider and text-to-speech of one fixed-format sentence. No conversation, no recording.

**Risks.**
- *False alarms destroy trust.* Strict rules for what qualifies, a per-week cap, quiet hours, and a "was this worth a call?" button that feeds precision into the report card.
- *Telephony cost and number verification:* verify the owner's number with a one-time code over a trusted channel, using the same linking pattern as identity.
- *Later errand calls:*
  - an AI disclosure in the first seconds;
  - the business's opt-out;
  - a live transcript to the owner;
  - no recording without consent (PDPA; US two-party-consent states).

**Smallest testable version (1–2 weeks).** A "call me for critical" setting with one rule type (VIP plus keyword), a call with fixed text and DTMF acknowledgement, and an acknowledgement logged in the audit trail.

### 12. Open agent-to-agent: "my Ava orders from the restaurant's agent" *(required: later-stage bet)*

**User story (2027+).**
- Ananya says "order my usual from Tiong Bahru for 1pm".
- His Ava finds the restaurant's A2A Agent Card and sends a structured order using his food preferences, which it shares only as the order's audience allows (allergies, yes; anything else, no).
- It pays under an AP2-style mandate the owner signed in advance: "up to S$40 per meal, only at these merchants, only weekdays".
- Every external agent's reply is treated as untrusted content.

**Why it spreads.** It is the "superintelligence era" demo everyone wants to film, and it would be highly viral once it works reliably.

**What makes it a fit later.**
- Ava's existing pieces map directly onto what Know-Your-Agent and AP2 need:
  - identity by immutable IDs;
  - authority from the database;
  - side effects behind approvals;
  - a spend cap.
- An *Adult*-stage Ava with a mandate is exactly "earned autonomy" applied to money.

**Risks (why it's last).**
- *The protocols are still being standardised*, with no public go-live data.
- *Payment liability and fraud* are significant.
- *Injection between agents* is a documented attack surface (Moltbook).
- *Restaurant agents mostly don't exist yet.*
- *The owner explicitly asked not to overcomplicate the early stage.*

**Smallest testable version (2 weeks, non-functional).**
- A design document that maps the stage gates to mandate limits.
- A read-only, published Ava Agent Card that says what Ava accepts (for example, scheduling requests from verified agents, as in idea 10) and refuses everything else.
- No payment code.

---

## Part 3: Ideas considered and rejected

- **A hardware pendant or glasses app.** The evidence is that standalone AI hardware fails or is acquired (Humane, Limitless, Friend), and ambient capture is a litigation target. Glasses volume belongs to Meta.
- **An "AI employee" leaderboard with salaries.** The 11x/Artisan lesson in the marketplace report applies; the report card measures outcomes instead.
- **A public feed of Ava conversations.** Meta's Discover feed shows the failure mode. Only aggregates and owner-approved lessons are ever public.
- **A companion or personality mode for engagement.** Character.AI's lawsuits, and the charter's "no claims of feelings", rule this out.
- **WhatsApp via unofficial libraries.** Ban risk, plus Meta's 2026 terms. The EU paid route (€0.05–0.13 per message) could be revisited for EU owners later.
- **An open skills marketplace.** OpenClaw's 230+ malicious skills; ADR 0006's signed two-tier model stays.

## Part 4: Hype versus evidence

| Claim you'll hear | What the evidence supports |
|---|---|
| "Personal agents are going mainstream" | Open-source agents went viral with developers (OpenClaw, Hermes). Consumer proactive agents remain paid-tier, US-first previews (Pulse Pro-only, Spark Ultra-only, Call for Me Pixel-only). We found no public usage numbers for ChatGPT's agent mode. |
| "Agents will transact with each other" | The standards (A2A, AP2, TAP, KYA) are real and well-backed. Transaction volume and merchant adoption aren't public. Treat this as 2027+. |
| "Voice is the interface of the AGI era" | Viral demos (Sesame) and platform launches are real. Consumer call screening is a forecast. Asynchronous voice notes are the proven habit. |
| "Wearables capture your whole life" | Glasses sell, on Meta's platform. Standalone pendants failed or were acquired. |
| "Trust is the differentiator" | Mixed but encouraging. Claude's 2026 growth spike rode a values story, and Gandalf shows security can be a game. No assistant has yet grown on *verifiable privacy*, so ideas 3 and 4 are a bet, not a proven playbook. |
| "Recipients convert like Calendly invitees" | The Calendly loop is well documented. For AI-native products like Granola, recipient-to-user conversion isn't public. Idea 1 should be instrumented from day one to find out. |

**Source caveats.** OpenClaw and Hermes star counts disagree across sources by up to 10×. The Spark price, Rabbit's 2026 status and some Calendly ARR figures are single-sourced or AI-generated summaries. The 2026 status of Pulse could not be confirmed beyond "not yet for everyone".

## Sources

**Viral agents and consumer assistants**
- OpenClaw statistics: https://www.getpanto.ai/blog/openclaw-ai-platform-statistics
- OpenClaw trend (Spanish): https://similarlabs.com/es/blog/openclaw-ai-agent-trend-2026
- OpenClaw security and enterprise use: https://www.letsdatascience.com/news/openclaw-advances-agentic-ai-raises-security-concerns-fb751eba
- OpenClaw/Moltbot security concerns: https://thenewstack.io/openclaw-moltbot-security-concerns/
- Hermes Agent breakdown: https://www.vellum.ai/blog/official-hermes-agent-breakdown
- Hermes Agent review: https://www.heyuan110.com/posts/ai/2026-04-14-hermes-agent-guide
- Hermes alternatives: https://blog.buildbetter.ai/best-hermes-agent-alternatives-in-2026-local-first-personal-ai-agents-compared/
- Claude consumer growth: https://techcrunch.com/2026/03/06/claudes-consumer-growth-surge-continues-after-pentagon-deal-debacle/
- Claude app revenue: https://appfigures.com/resources/insights/claude-app-revenue-up-2500
- Claude paying consumers: https://thenextweb.com/news/anthropic-claude-paying-consumers-growth-chatgpt-indagari
- Sesame: https://research.contrary.com/company/sesame-ai
- Sesame reaction: https://www.pcworld.com/article/2623695/i-was-so-freaked-out-by-talking-to-this-ai-that-i-had-to-leave.html
- Gandalf: https://www.lakera.ai/news/gandalf-new-ui-and-enhanced-ai-security-education
- Gandalf red-team data: https://zilliz.com/blog/insights-into-llm-security-from-the-worlds-largest-red-team
- ChatGPT Pulse launch: https://techcrunch.com/2025/09/25/openai-launches-chatgpt-pulse-to-proactively-write-you-morning-briefs
- Pulse availability: https://www.sentisight.ai/when-will-chatgpt-pulse-be-available-for-plus-and-free-users/
- Gemini Personal Intelligence: https://dataconomy.com/2026/01/15/gemini-gains-personal-intelligence-to-synthesize-data-from-gmail-and-photos/
- Gemini Personal Intelligence: https://aibusiness.com/agentic-ai/gemini-scours-apps-personal-intelligence
- Gemini Spark: https://siliconangle.com/2026/05/19/google-unveils-gemini-spark-always-ai-agent-daily-digital-tasks/
- Gemini Spark: https://thenextweb.com/news/google-gemini-spark-agentic-assistant-gmail-io-2026
- Meta AI Discover feed: https://www.mozillafoundation.org/en/campaigns/meta-help-users-stop-accidentally-sharing-private-ai-conversations/
- Meta AI Discover feed: https://androidauthority.com/meta-ai-sharing-chats-public-feed-3567084
- Character.AI teen ban: https://abc17news.com/money/cnn-business-consumer/2025/10/29/after-a-wave-of-lawsuits-character-ai-will-no-longer-let-teens-chat-with-its-chatbots/
- Character.AI lawsuit: https://www.popsci.com/technology/character-ai-teen-lawsuit/
- Granola collaboration features: https://techcrunch.com/2025/05/14/ai-note-taking-app-granola-raises-43m-at-250m-valuation-launches-collaborative-features
- Granola sharing docs: https://help.granola.ai/article/sharing-notes
- Calendly viral loop: https://openviewpartners.com/blog/saas-product-viral-loop/
- Calendly product-led growth: https://elevationcapital.com/calendly-product-led-growth

**Wearables and devices**
- Friend pendant: https://www.fortune.com/2025/10/01/who-is-avi-schiffmann-friend-ai-pendant-necklace
- Friend subway campaign: https://www.contentgrip.com/friend-nyc-subway-campaign/
- Meta acquires Limitless: https://betanews.com/2025/12/08/meta-has-acquired-limitless-a-startup-that-produces-ai-powered-wearables/
- Meta acquires Limitless: https://www.emarketer.com/content/meta-acquires-ai-wearables-startup-limitless-fuel-device-ambitions
- Humane shutdown: https://www.fortune.com/2025/02/19/hp-humane-deal-ai-pin-shutting-down
- Rabbit after Humane: https://www.techradar.com/computing/artificial-intelligence/with-the-humane-ai-pin-now-dead-what-does-the-rabbit-r1-need-to-do-to-survive
- Rabbit vs Humane: https://www.layer3labs.io/comparisons/rabbit-r1-vs-humane-ai-pin
- Ray-Ban sales: https://t2online.in/tech/tech-news/essilorluxottica-s-sales-soar-as-meta-partnership-fuels-ai-smart-glasses-revolution/2003603

**Voice and phone**
- Gemini Call for Me: https://techcrunch.com/2026/09/24/google-tests-letting-gemini-make-phone-calls-initially-for-us-pixel-owners/
- Gemini calling businesses: https://www.localfalcon.com/blog/googles-gemini-is-starting-to-call-local-businesses-for-customers
- AI calling comparison: https://andrew.ooo/answers/gemini-call-for-me-vs-alexa-plus-vs-chatgpt-voice-which-ai-phones-businesses-september-2026/
- Agent-answered calls forecast: https://guptadeepak.com/future-tech/agent-answered-calls/

**Agent-to-agent, payments and identity**
- AP2: https://www.mintmcp.com/blog/ap2-agent-payments-protocol
- Agent payment protocols compared: https://eco.com/support/en/articles/14839407-agent-payment-protocols-compared
- Payment protocol competition: https://blockeden.xyz/blog/2026/03/14/payment-giants-agent-protocol-war-visa-tap-google-ap2-coinbase-x402-paypal-ai-commerce/
- Visa Trusted Agent Protocol: https://kapronasia.com/insight/blogs/payments-research/visa-and-cloudflare-launch-trusted-agent-protocol
- Know-Your-Agent collaboration: https://www.uniindia.com/business-wire-india/ant-international-mastercard-and-visa-initiate-collaboration-on-know-your-agent-interoperability-to/174745

**Trust and safety incidents**
- Moltbook and OpenClaw threats: https://www.axios.com/2026/02/03/moltbook-openclaw-security-threats
- Moltbook exposure: https://www.bankinfosecurity.com/moltbook-gave-everyone-control-every-ai-agent-a-30710
- Moltbook database leak: https://www.gigazine.net/gsc_news/en/20260202-moltbook-database-leaked

**AI employees**
- Klarna reversal: https://www.entrepreneur.com/business-news/klarna-ceo-reverses-course-by-hiring-more-humans-not-ai/491396
- Klarna reversal: https://www.maginative.com/article/klarna-dials-back-its-ai-customer-service-strategy-now-its-hiring-humans-again/
- Humans behind x.ai's Amy: https://www.bloomberg.com/news/articles/2016-04-18/the-humans-hiding-behind-the-chatbots

**Global channels and models**
- WhatsApp chatbot ban: https://techcrunch.com/2025/10/18/whatssapp-changes-its-terms-to-bar-general-purpose-chatbots-from-its-platform
- WhatsApp Brazil exemption: https://techcrunch.com/2026/01/15/after-italy-whatsapp-excludes-brazil-from-rival-chatbot-ban
- WhatsApp EU fee: https://techcrunch.com/2026/03/05/meta-will-allow-rival-ai-chatbots-on-whatsapp-in-europe-but-for-a-fee/
- SEA-LION community: https://aisingapore.org/news/sea-lion-inspires-community-to-build-ai-solutions-that-solve-real-world-challenges-in-southeast-asia/
- SEA-LION background: https://www.scmp.com/news/asia/southeast-asia/article/3251353/singapore-builds-chatgpt-alike-better-represent-southeast-asian-languages-cultures

**Internal**: [Semi human AI agent marketplace](Semi%20human%20AI%20agent%20marketplace.md), [Humanistic assistant architecture review](Humanistic%20assistant%20architecture%20review.md), [Ava skills tools and cited memory](Ava%20skills%20tools%20and%20cited%20memory.md), `.delivery/product-vision.md`, ADRs 0006–0008, `docs/overnight-log.md`.
