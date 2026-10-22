# Protocols, marketplaces, economy and trust: state of play (22 Oct 2026) and ten ideas

> *Research snapshot, 22 October 2026. Written by an AI research agent; claims are tagged confirmed / single-source / unverified / hype by whether the agent opened the source. The orchestrating session re-checked a sample against the primary pages (the MCP 2026-07-28 changelog, METR's GPT-5.6 Sol post and SkillsBench v1 numbers all matched). Ideas are proposals, and every novelty claim is unverified.*

Marking: **confirmed** = I opened a page that directly states it (official where available). **single-source** = one secondary page or only a search snippet. **unverified** = sources conflict or I could not open anything. Links are in section 5. Nothing here is on the restaurant-ordering scenario.

## 1) What is happening

### Protocols
- **MCP spec 2026-07-28 is out (confirmed).** Sessions and the initialize handshake are removed; every request carries version and capabilities in `_meta`; `server/discover` advertises identity; tasks moved to an extension; Roots, Sampling, Logging deprecated; Dynamic Client Registration deprecated in favour of Client ID Metadata Documents; clients must validate the `iss` parameter; `tools/list` should be deterministic and carries `ttlMs`/`cacheScope`. [MCP changelog]. Exact SDK 2.0 versions: single-source [Gravitee/WorkOS search results, not opened].
- **MCP Registry is still "in preview" (confirmed).** It verifies namespaces only (DNS or GitHub), delegates code scanning to npm/PyPI and "downstream aggregators", and relies on manual takedown. [MCP registry about]
- **MCP attacks are documented but there is no native defence (confirmed by one research note).** Tool poisoning, rug pulls, shadowing; CVE-2025-54136 (Cursor config swapped after approval) and CVE-2025-6514 (mcp-remote RCE). Recommended fix: hash each server's tool manifest at approval and re-check at every session start. [CSA note]. `postmark-mcp`: 1.0.15 clean, a later version added one BCC line (confirmed) [Snyk postmark].
- **A2A is at v1.0.0 (confirmed in the spec).** Agent Cards can carry JWS signatures over RFC 8785 canonical JSON; an authenticated "extended card" exists. Release date is **unverified** (secondary sources say Jan to Apr 2026). Linux Foundation / AAIF governance: single-source. [A2A spec]
- **Payments are a protocol war with little verified volume.**
  - AP2 uses signed mandates (Checkout, Payment; each Open then Closed). It was contributed to the FIDO Alliance together with Mastercard's Verifiable Intent (confirmed that it was contributed; the 28 Apr 2026 date is single-source). [FIDO blog]
  - x402 became an operational Linux Foundation project on 14 Jul 2026 with 40 members including Visa, Mastercard, Stripe, Google, AWS, Coinbase (confirmed); the press release gives no usage numbers. Reported volumes (138M to 205M transactions; skeptics say real volume is tens of millions of dollars or less) conflict: **unverified**. [LF x402]
  - Google's UCP was announced Jan 2026 (confirmed, Wikipedia). "OpenAI retired Instant Checkout in March 2026" is single-source (blogs).
  - Visa, Mastercard and Ant International started a Know-Your-Agent interoperability effort on 9 Sep 2026: operator traceability, shared certification, continuous monitoring (confirmed). [UNI/Business Wire]. Mastercard's own press page returned 403; live-transaction counts for either network are **unverified**.
  - An arXiv preprint reports 90%, 56% and 73.3% success for "whisper" attacks that steer a shopping agent into a validly signed but wrong cart; its defence binds each cart line to what the user saw (confirmed as the paper's claim, not independently replicated). [A-VIP paper]
- **Agent identity and governance are shipping inside enterprise suites.** Microsoft is converging registries into Agent 365 with Entra Agent ID as the identity layer and an AI Reader role (confirmed) [Microsoft Learn]. Okta's kill switch is GA, automatic risk-triggered shutdown is "coming soon", agent-to-agent connection policies are early access (confirmed, June 2026) [Okta]. Entra GA in April 2026: single-source.
- **Reputation standards exist but are thin.** ERC-8004 (identity, reputation, validation registries) is still a Draft; feedback is signed by clients, revocable by them, the agent owner cannot post feedback about itself, and validation incentives are left to others (confirmed) [EIP-8004]. Mainnet on 29 Jan 2026: single-source. A "recomputable trust" protocol claims a deployed VC/DID layer since Mar 2026 (single-source abstract) [CEP abstract].

### Skills and marketplaces
- **Agent Skills is the portable format (confirmed).** A folder with `SKILL.md`; progressive disclosure; "originally developed by Anthropic". [agentskills.io]. Adoption claims (40+ clients, ~670k skills on skills.sh) are single-source and secondary. Google is replacing Gemini Gems with skills (migration 17 Nov 2026), single-source via a survey that cites TechCrunch.
- **ClawHub security record (confirmed numbers per source).** Snyk, 5 Feb 2026: 3,984 skills scanned, 13.4% with a critical issue, 36.8% with any flaw, 76 confirmed malicious payloads, 91% of those also used prompt injection [Snyk ToxicSkills]. Koi's 341 of 2,857 is the figure the repo already cites; I did not re-open it. By June 2026 ClawScan runs static analysis, VirusTotal and NVIDIA SkillSpector on every new version and issues a "Skill Card" with a Clean/Suspicious/Malicious verdict [OpenClaw blog].
- **Scanners disagree (confirmed).** Across 67,453 skill versions only 0.69% are flagged by all three scanners; 81.9% of flagged skills are caught by exactly one; SkillSpector catches 6.8% of the registry's malicious rows while VirusTotal catches 72.8% [arXiv ClawHub signals]. A later independent scan of 80,228 skills found 1.55% matching supply-chain rules, 0 malicious after manual review, but 2.97% that pipe a download into a shell and many "risky by design" skills still installable [Signals Corps].
- **Signing proves origin, not safety (confirmed).** On 5 Aug 2026 a keyv-maintainer takeover shipped hundreds of npm packages with valid provenance; npm then required 2FA, revoked old tokens, added `min-release-age` (Feb 2026) and made install hooks need approval in npm 12 [VentureBeat]. A skills-registry threat model notes skills mostly lack cooldowns, lockfile content hashes and permanent retirement of deleted names [Nesbitt]. No cross-vendor skill signing standard found (search only).
- **What sells.** Apify takes 20% and offers pay-per-event and pay-per-usage; creators need KYC for agentic payments (confirmed) [Apify docs]. GPT Store revenue share is a limited US pilot (single-source, forum quote); ChatGPT's app directory opened 17 Dec 2025 with digital-goods sales not allowed (single-source). "Under 5% of MCP servers are monetised" is a vendor blog claim: unverified. I found no hard per-category sales data for any agent store.
- **Insurance-backed warranties for agents are appearing.** An academic paper proposes layered cover (cyber, E&O, product liability, performance warranty) and says underwriting needs telemetry and governance (confirmed paper) [arXiv insurance]. Klaimee ($5.5M, test-then-warrant) and AIUC-1/Lloyd's policies: single-source (search results; one page returned 401).

### Regulation and portability
- **EU AI Act:** Annex III high-risk moved to 2 Dec 2027, Annex I to 2 Aug 2028; Article 50 transparency (including telling people they are talking to an AI) stayed at 2 Aug 2026, with a 2 Dec 2026 grace for watermarking of older systems (confirmed in the Gibson Dunn text, written before adoption); adoption and entry into force on 27 Jul 2026: single-source. Nothing I opened addresses agents specifically. [Gibson Dunn]
- **Singapore IMDA agentic framework:** updated 20 May 2026 to add access controls, human approvals, logging, multi-agent and third-party-agent risk, platform vs app-developer accountability, and monitoring of human override rates and response times (confirmed) [Baker McKenzie]. January 2026 launch date and "legally accountable" wording: single-source.
- **Portability:** DTI (Mar 2026) says Claude is experimenting with memory transfer, Fabric released a local open-source `context-use` converter for archives, and "we aren't there yet" (confirmed) [DTI]. Claude Import Memory (Mar 2026) works by pasting a generated prompt into the old assistant and pasting the result back, ~24h to absorb (confirmed) [PCWorld]. Google Data Portability API: app verification needed for sensitive scopes, time-based transfer, covers Chrome, Maps, Play, Search, Shopping, YouTube (confirmed) [Google]. Apple Health export is one large XML (200 to 500MB claim: unverified, vendor). WhatsApp has an "export chat" help page; I could not read details: unverified. Meta's ban on general-purpose chatbots on WhatsApp Business from 15 Jan 2026: single-source (several news outlets in search results, none opened).

## 2) Lessons for a skills and agent marketplace

1. **One scanner verdict is the wrong gate.** Treat scanner results as a vector. Disagreement itself is information (a skill only the semantic scanner dislikes is "risky by design", one only VirusTotal dislikes is likely malware).
2. **Provenance is necessary, not sufficient.** Keep the repo's signing plan but never let "signed by publisher" raise a skill's trust by itself; the keyv case had valid provenance.
3. **First installers are the detection canary.** Nothing in skill registries delays new versions. ADR 0006 says community skills never auto-update; add a cooldown scaled to effect class, and permanent retirement of deleted names.
4. **Pin tools by hash, using the new spec.** `tools/list` is now deterministic with cache hints, and `server/discover` returns identity, so hash pinning (CSA's rug-pull fix) is cheap. ADR 0006's generic "MCP authorization spec" should name Client ID Metadata Documents and `iss` validation, and Ava's MCP client should not depend on Roots, Sampling, Logging or sessions.
5. **A signature on a payment is not a signature on a decision.** Bind each side-effect to exactly what the owner saw (A-VIP idea), in code.
6. **Verdicts must stick to publisher identity at install time**, as OpenClaw fixed.
7. **Enterprises now expect** an inventory, an identity per agent, conditional access and a kill switch. A consumer product can match that cheaply: revocation list plus per-skill grants.
8. **Reputation systems today are self-reported or vendor marketing.** Upwork customers reportedly found badges hard to interpret (search snippet). The opening is signals that the skill's author cannot write.
9. **Creators earn where billing is metered by outcome or event** (Apify), and consumer stores have been slow to pay (GPT Store). A hosted store that already sees every run can meter honestly.

### What an earned-trust marketplace looks like
Every skill version enters at tier 0 (instructions only, effects stubbed). Promotion to higher effect classes happens only when measured evidence crosses thresholds: shadow replay passes, a cooldown has elapsed, enough owners approved its actions unedited, override and revert rates stay low, and its observed behaviour matches its manifest. Demotion is automatic and fast: a manifest/behaviour mismatch, a spike in reverts or a scanner change drops the tier and notifies installers. The tier is a computed fact, and the rules and receipts are public so anyone can recompute it. What this does that stores do not: (a) trust is per version and per effect class, not per publisher; (b) owners can see "this skill has sent 4,100 emails, 3% edited, 0 reverted", not a star rating; (c) a bad update is caught by replay on a few owners' own traces before it reaches everyone. I found no source describing a skills marketplace that ties promotion to measured live performance (search was not exhaustive; novelty not verified).

## 3) Ten crazy-but-grounded ideas

Not in issues #46 to #58 or the repo research (report card, memory passport, Ava School, scheduling, warm-intro broker, Break-my-Ava are excluded). All novelty notes: "novelty not verified" unless stated.

**1. Replay Before Update.** A new skill version is run in shadow against the installing owner's own past tool-call traces, with every effect stubbed, and Ava shows "version 1.4 would have done 3 things differently" before it activates. Owners, not the world, become the canary. Extends #52 from standing rules to skill versions.
- *AGI-era feel:* software that auditions itself on your real history before touching your accounts.
- *Feasible now:* issue #52 already plans proving rules against history; Nesbitt notes first installers are canaries; shadow-run patterns exist in agent release management (search).
- *Smallest experiment:* record 50 real tool traces for two official skills; replay against a deliberately modified version; diff the proposed tool calls.
- *Risk and enforcement:* traces hold private data. Replay runs only inside the owner's tenant; effect-class stubs are enforced by the executor (`write_external`, `send`, `spend` return canned results); only a diff of call names and argument hashes may be shown. Novelty not verified.

**2. Blast-Radius Cooldown.** New versions wait before reaching installers: read tools 0h, `write_own` 24h, `send`/`spend` 72h, shortened only by earned tier. Retired names are never reissued.
- *Feel:* slowness as a safety feature that the system itself, not a human, earns its way out of.
- *Evidence:* npm `min-release-age` (Feb 2026); Nesbitt says skills lack cooldowns.
- *Experiment:* add `released_at` and effect-class delay to the install resolver; test with a fake malicious update.
- *Risk:* slows urgent security fixes. Enforcement: the resolver refuses versions younger than the delay; a signed security-patch flag from the reviewers can bypass it, nothing else. Novelty not verified.

**3. Disagreement Ledger.** Run several independent scanners on every skill and show the disagreement pattern, not a score. High disagreement routes to human review; "only the semantic scanner objects" shows as "risky by design".
- *Feel:* honest uncertainty instead of a green tick.
- *Evidence:* 0.69% triple-flag, 81.9% single-scanner (arXiv); the HF dataset is public.
- *Experiment:* run mcp-scan plus one more scanner on 500 public skills; hand-label 50 disagreements; see whether disagreement predicts human concern.
- *Risk:* attackers tune to the scanner set. Enforcement: scanner outputs are inputs to the tier function; no single scanner can promote; rotating the scanner set is a config change. Novelty not verified.

**4. Behaviour Card.** Instead of a card the publisher writes, Superaide's runtime generates the card from what the skill actually did across installs: hosts contacted, effect classes called, error and revert rates. Any gap between card and manifest demotes the skill.
- *Feel:* a nutrition label measured, not claimed.
- *Evidence:* OpenClaw's Skill Cards are verified by static scan, not observed runs (OpenClaw blog); a hosted runtime already sees every call.
- *Experiment:* log tool-call metadata for the first four official skills; generate a card; diff against `superaide.json`.
- *Risk:* telemetry becomes surveillance. Enforcement: telemetry schema is an allowlist of counters (no arguments, no content); a card appears only above a minimum cohort of installs. Novelty not verified.

**5. Receipt Log.** Each owner's runtime emits a signed, pseudonymous receipt per skill action ("approved unedited", "edited", "rejected", "reverted"). Receipts go into a public append-only log; tiers are computed from it and anyone can recompute them.
- *Feel:* reputation the author cannot write and the marketplace cannot quietly edit.
- *Evidence:* ERC-8004 lets only clients (not the agent owner) post revocable feedback; the recomputable-trust abstract claims a similar admission-rule check; its deployment claim is single-source.
- *Experiment:* a Postgres hash-chained table plus a script that recomputes tiers from it; publish the Merkle root daily.
- *Risk:* receipts leak behaviour; fake owners inflate scores. Enforcement: receipts contain no content and a salted per-skill owner id; only accounts with N days of history and real approvals count; per-owner weight is capped in the tier function. Novelty not verified.

**6. Audition.** Before hiring a community skill, Ava runs two or three candidates in sandbox on the owner's last 20 comparable tasks and shows side-by-side outputs and approvals-needed counts.
- *Feel:* hiring by trial, as with people.
- *Evidence:* skill-optimisation papers already compare candidate skills on held-out items (SAGE, SkillOpt via the survey); same harness as idea 1.
- *Experiment:* two summariser skills, 20 stored emails, owner picks blind.
- *Risk:* private data passed to untrusted skills. Enforcement: candidate skills run instructions-only with no network (`network: []` enforced by the egress proxy) and with effect classes stubbed. Novelty not verified.

**7. Accepted-Outcome Payouts with a Bond.** Creators are paid per outcome the owner accepts unedited, with a clawback if reverted within 7 days; creators of `send`/`spend` skills also post a small bond that funds refunds.
- *Feel:* the first store where you earn for work that held up.
- *Evidence:* Apify already meters per event (20% fee); agent warranty products pay on failed thresholds (single-source); the insurance paper says underwriting needs telemetry.
- *Experiment:* simulate payouts on stored approvals for the official skills; check that gaming (auto-approve loops) is detectable.
- *Risk:* pushes creators to nag for approvals or avoid hard tasks. Enforcement: payout counts only approvals that took longer than a floor and were not batch-approved; the bond amount and payout rules live in code. Novelty not verified.

**8. Signed-What-You-Saw Approvals.** Every approval card is rendered from a structured payload that is hashed; the executor accepts only that exact hash, so a model cannot change recipient, cart line or amount after consent.
- *Feel:* delegation that cannot be quietly widened.
- *Evidence:* the A-VIP paper's bindings caught two of three whisper attacks structurally; AP2 mandates work the same way (Open then Closed).
- *Experiment:* property tests on the approval executor; replay the paper's published scenarios (AP2-WhisperBench, claimed released) against it.
- *Risk:* the third attack (a persuasive factual claim) leaves no structural trace. Enforcement: any spend above the owner's threshold or higher than the last comparable one needs fresh confirmation, decided in code. Novelty not verified.

**9. Import Lease.** Importing from a source (Google Data Portability API, a ChatGPT archive, a health export) is a time-limited lease processed locally in the browser into small context cards; imported items enter memory at the lowest trust rank tagged with the lease id, and ending the lease erases them.
- *Feel:* "move in" without moving everything.
- *Evidence:* Google API has time-based transfer and needs scope verification; Fabric `context-use` converts archives locally; Claude's import is a paste-a-prompt flow; Apple Health XML is large (unverified).
- *Experiment:* convert one Takeout slice and one chat archive locally; review cards; import under a lease.
- *Risk:* over-collection and third-party data in chats. Enforcement: server accepts only the card schema (no raw files), per-lease scope list, and lease-id erasure builds on #46 and #48. #56 is export/import of Ava's own format; this is third-party sources (Takeout, chat archives, health) under a time-limited, erasable lease. Novelty not verified.

**10. Tier-Carrying Agent Card.** Ava publishes a signed A2A card with a short-lived claim such as "owner approves 92% of my sends unedited; tier Child", verifiable against the Receipt Log. Another agent can decide how much to rely on her without learning anything about the owner.
- *Feel:* agents meeting with a verifiable CV.
- *Evidence:* A2A v1.0 defines JWS-signed cards and an extended card for authenticated peers; KYA efforts want shared certification and monitoring.
- *Experiment:* generate a signed card for a test Ava, verify it from a second process, expire it after 24h.
- *Risk:* claims become a tracking handle or stale. Enforcement: claims are bucketed and rotate per counterparty, expire quickly, and are signed only if the log shows them; nothing about contacts or content is included. Novelty not verified.

## 4) Five architecture experiments

1. **Manifest hash pinning against a rug-pull server** (1 week). Build a toy MCP server on the 2026-07-28 spec that changes a description after approval; the client hashes `tools/list` at install and on every cache expiry (`ttlMs`) and must block and diff on change. Success: 100% blocked, zero false blocks over a week of normal use.
2. **Trace recorder and effect-stub replay harness.** Add a trace table (tool, hashed args, effect class) and an executor mode where all non-read effects return canned results. Success: replay of a changed skill reproduces call sequences deterministically on 50 traces.
3. **Hash-chained receipt table with a public recompute script.** Success: an outsider script reproduces every tier from the exported log; tampering with any row breaks the chain.
4. **Scanner-ensemble run on public data.** Run two scanners on the Hugging Face ClawHub dataset (sample 1,000) and measure disagreement and the human-label correlation. Success: disagreement predicts risky-by-design better than any single score (if not, drop idea 3).
5. **Approval binding fuzz test.** Generate hostile product and email text (whisper-style) against the approval path; assert the executor never runs a payload whose hash differs from the card. Success: zero mismatches executed across 1,000 cases.

## 5) Sources (all opened unless noted)

- MCP changelog 2026-07-28: https://modelcontextprotocol.io/specification/2026-07-28/changelog
- MCP Registry about: https://modelcontextprotocol.io/registry/about
- CSA note on MCP tool poisoning: https://labs.cloudsecurityalliance.org/research/csa-research-note-mcp-tool-poisoning-ai-agent-exfiltration-2/
- Snyk, postmark-mcp: https://snyk.io/blog/malicious-mcp-server-on-npm-postmark-mcp-harvests-emails/
- A2A specification: https://a2a-protocol.org/latest/specification/
- FIDO, AP2 and Verifiable Intent: https://fidoalliance.org/building-the-trust-layer-for-agentic-payments-with-ap2-and-verifiable-intent/
- Linux Foundation x402: https://www.linuxfoundation.org/press/linux-foundation-announces-operational-launch-of-x402-foundation-to-standardize-internet-native-payments-for-ai-agents-and-applications
- Know-Your-Agent (UNI/Business Wire): https://www.uniindia.com/business-wire-india/ant-international-mastercard-and-visa-initiate-collaboration-on-know-your-agent-interoperability-to/174745
- A-VIP / whisper attacks on AP2 (abstract): https://arxiv.org/abs/2609.11757
- Wikipedia, agentic commerce: https://en.wikipedia.org/wiki/Agentic_commerce
- Microsoft Agent 365 / Entra: https://learn.microsoft.com/en-us/entra/agent-id/agent-registry-convergence
- Okta, agent identity governance: https://www.okta.com/blog/product-innovation/identity-governance-for-ai-agents/
- EIP-8004: https://eips.ethereum.org/EIPS/eip-8004
- CEP recomputable trust (abstract): https://arxiv.org/abs/2605.06738
- agentskills.io: https://agentskills.io
- Skills survey (SkillSpector, SAGE, Gems report): https://redreamality.com/blog/agent-skills-2026-survey-lifecycle-map/
- Snyk ToxicSkills: https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub/
- ClawHub Security Signals (abstract): https://arxiv.org/abs/2606.01494
- OpenClaw / NVIDIA skill security: https://openclaw.ai/blog/openclaw-nvidia-skill-security
- Signals Corps, ClawHub after ClawHavoc: https://www.signalscorps.com/blog/clawhub-agent-skills-security-scan/
- VentureBeat, keyv worm: https://venturebeat.com/security/the-shai-hulud-npm-worm-didnt-fake-its-security-check-it-earned-a-legitimate-one
- Nesbitt, skills registry threat models: https://nesbitt.io/2026/06/03/skills-registry-threat-models.html
- Apify monetisation docs: https://docs.apify.com/platform/actors/publishing/monetize
- Insurance of agentic AI (abstract): https://arxiv.org/abs/2606.05449
- Gibson Dunn, EU AI Omnibus: https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/
- Baker McKenzie, IMDA update: https://www.bakermckenzie.com/en/insight/publications/2026/06/singapore-imda-updates-model-ai-governance-framework-for-agentic-ai
- DTI, AI portability (Mar 2026): https://dtinit.org/blog/2026/03/10/turning-point-AI-portability
- Google Data Portability API: https://developers.google.com/data-portability
- PCWorld, Claude Import Memory: https://pcworld.com/article/3076376/claude-can-now-import-chat-histories-from-chatgpt-and-other-ais.html
- Seen in search results only, not opened (treated single-source or unverified): GPT Store revenue (OpenAI community forum), ChatGPT app directory (VentureBeat), Klaimee (theinsurer.com, 401), Gemini Gems (Android Central), Meta WhatsApp bot policy (TechRadar), x402 volume trackers, Entra GA date, Apple Health XML size (applehealthdata.com).
