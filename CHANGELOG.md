# Changelog

Every change to `main` arrives through a pull request, so this is the audit trail of what changed, when and why.
Each line links to its pull request, which has the review notes, the before and after screenshots and the test output.

How it is kept: one entry per merged pull request, newest first, grouped by day and type (Added, Fixed, Tests, ...).
After you merge, run `scripts/changelog.py` (it rewrites this file from GitHub's merged pull requests), and publish a
GitHub Release with generated notes (`gh release create vYYYY.MM.DD --generate-notes`, grouped by `.github/release.yml`).

## Unreleased

### 2026-10-24

**Maintenance**
- ci: make end-to-end advisory on pull requests ([#397](https://github.com/BeastxD7/superaide/pull/397))
- ci: define the tests-not-needed label and guard CI labels ([#376](https://github.com/BeastxD7/superaide/pull/376))
- ci: map test_slack_owner_channels.py (main is red on the module map test) ([#373](https://github.com/BeastxD7/superaide/pull/373))
- ci: map the tests added by #361 and #363 (main is red on the module map test) ([#371](https://github.com/BeastxD7/superaide/pull/371))

**Added**
- feat(mandate): a covering signed mandate skips the card for own-calendar writes (#112) ([#390](https://github.com/BeastxD7/superaide/pull/390))
- feat(triage): owner-promoted, raise-only kNN head with a Promote control (#117) ([#395](https://github.com/BeastxD7/superaide/pull/395))
- feat(voice): Deepgram realtime adapter and a separate monthly voice cap inside the spend cap (#125) ([#392](https://github.com/BeastxD7/superaide/pull/392))
- feat(school): playbook text, per-lesson eval and an in-prompt switch that needs a passing eval (#57) ([#391](https://github.com/BeastxD7/superaide/pull/391))
- feat(import): Hermes layout, dry run, list-only skills and a names-only layout listing (#141) ([#389](https://github.com/BeastxD7/superaide/pull/389))
- feat(principal): opt-in per owner, first name plus chosen initial, and experiment counters (#135) ([#388](https://github.com/BeastxD7/superaide/pull/388))
- feat(agent): human-like task protocol behind TASK_PROTOCOL_ENABLED ([#386](https://github.com/BeastxD7/superaide/pull/386))
- feat(people): page /app/people by cursor, skeleton, one query per page, contact index (0082) ([#385](https://github.com/BeastxD7/superaide/pull/385))
- feat(triage): superaide triage-knn-compare, replay the owner's labelled history through the kNN head (#117) ([#384](https://github.com/BeastxD7/superaide/pull/384))
- feat(console): the Safety log pages by cursor with a skeleton and Load more ([#383](https://github.com/BeastxD7/superaide/pull/383))
- feat(mandate): shadow check, record whether a mandate would have covered each approval (#112) ([#381](https://github.com/BeastxD7/superaide/pull/381))
- feat(privacy): Private Lobe boundary, only a bool, a listed value or a whole number crosses (#128) ([#382](https://github.com/BeastxD7/superaide/pull/382))
- feat(logs): page /api/audit and /app/access-log by cursor, audit_log indexes (0081) ([#377](https://github.com/BeastxD7/superaide/pull/377))
- feat(reminders): count pill, live list and toasts for reminders (#345) ([#375](https://github.com/BeastxD7/superaide/pull/375))
- feat(citations): drop marks a source does not back; deep links and a source note in channel footers (#341) ([#374](https://github.com/BeastxD7/superaide/pull/374))
- feat(slack): read the thread when the owner @-mentions Ava in it ([#370](https://github.com/BeastxD7/superaide/pull/370))

**Changed**
- Explicit send requests: draft and approval in one step, warning for automated recipients (#394) ([#396](https://github.com/BeastxD7/superaide/pull/396))
- test+fix(web): Needs-you keyboard run, axe on /a/<handle>, intro notices survive (#81) ([#387](https://github.com/BeastxD7/superaide/pull/387))

**Fixed**
- fix(evals): refuse to wipe any database that is not disposable ([#393](https://github.com/BeastxD7/superaide/pull/393))
- fix(slack): recognise the linked owner in channels and threads ([#366](https://github.com/BeastxD7/superaide/pull/366))

**Tests**
- test(web): Markdown safety, useReveal and keyboard-only introduction (#81) ([#379](https://github.com/BeastxD7/superaide/pull/379))
- test: make the deep-page latency and mobile doc-loader specs load-proof ([#378](https://github.com/BeastxD7/superaide/pull/378))
- test: fix two tests that fail just after midnight UTC (main is red) ([#372](https://github.com/BeastxD7/superaide/pull/372))

**Documentation**
- docs: update the changelog for the pull requests merged since 2026-10-22 ([#380](https://github.com/BeastxD7/superaide/pull/380))

### 2026-10-23

**Maintenance**
- ci: run only the tests of the modules a PR changes ([#367](https://github.com/BeastxD7/superaide/pull/367))
- chore(web): test the API proxy and security headers, remove unused console components (#80) ([#358](https://github.com/BeastxD7/superaide/pull/358))
- chore(docs): rename example name in the mobile audit table ([#355](https://github.com/BeastxD7/superaide/pull/355))
- chore: use fictional example names, never the owner's real name ([#349](https://github.com/BeastxD7/superaide/pull/349))
- ci: every push to main gets its own run ([#317](https://github.com/BeastxD7/superaide/pull/317))
- ci: split the backend job into backend-static and two pytest shards (stacked on #248) ([#272](https://github.com/BeastxD7/superaide/pull/272))
- ci: every feat and fix PR must ship tests (docs, template, CI check) ([#276](https://github.com/BeastxD7/superaide/pull/276))
- ci: smoke e2e for backend-only changes, guarded by an OpenAPI contract snapshot ([#248](https://github.com/BeastxD7/superaide/pull/248))
- ci: e2e in 4 shards balanced by measured time, cache the Next build ([#244](https://github.com/BeastxD7/superaide/pull/244))
- chore(web): one useResource hook; notifications opt-in in Settings behind the PWA flag ([#243](https://github.com/BeastxD7/superaide/pull/243))
- ci: run e2e and images beside backend, not after it ([#242](https://github.com/BeastxD7/superaide/pull/242))
- chore: remove stray sed backup file envelope.py-E ([#241](https://github.com/BeastxD7/superaide/pull/241))
- ci: cheaper runs, coverage only on main/Full, cached Playwright, never cancel main (PR 2/4) ([#235](https://github.com/BeastxD7/superaide/pull/235))
- ci: run only the jobs a change needs, behind one CI result gate (PR 1/4) ([#233](https://github.com/BeastxD7/superaide/pull/233))
- chore: integrate nine open PRs in one branch (one migration chain, one CI run) ([#229](https://github.com/BeastxD7/superaide/pull/229))
- chore(web): one errorMessage helper, browserTimeZone, proxy warning ([#186](https://github.com/BeastxD7/superaide/pull/186))

**Added**
- feat(ui): phone layout for the dashboard shell and pages ([#369](https://github.com/BeastxD7/superaide/pull/369))
- feat(console): conversations and people page by cursor with skeletons and Load more ([#364](https://github.com/BeastxD7/superaide/pull/364))
- feat(notebook): page /app/notebook by cursor, skeleton, server-side search, live-notes index (0080) ([#361](https://github.com/BeastxD7/superaide/pull/361))
- feat(channels): typing and working indicators on Telegram and Slack ([#362](https://github.com/BeastxD7/superaide/pull/362))
- feat(ui): phone layout for auth, hire, introduction and error pages ([#356](https://github.com/BeastxD7/superaide/pull/356))
- feat(operator): paged memories, approvals, people and unlinked accounts (0079) ([#354](https://github.com/BeastxD7/superaide/pull/354))
- feat(ui): compact phone navbar, touch-sized buttons, shared type scale and container ([#348](https://github.com/BeastxD7/superaide/pull/348))
- feat(console): redesign part 4: Simulator, break-glass and cleanup ([#346](https://github.com/BeastxD7/superaide/pull/346))
- feat(console): redesign part 3: People, Conversations, Safety log ([#336](https://github.com/BeastxD7/superaide/pull/336))
- feat(operator): page /api/events and /api/conversations by cursor, context.last_event_at (0078) ([#342](https://github.com/BeastxD7/superaide/pull/342))
- feat(console): redesign part 2: Overview and Spend ([#333](https://github.com/BeastxD7/superaide/pull/333))
- feat(web): the chat greets and offers quick actions only in an empty conversation ([#337](https://github.com/BeastxD7/superaide/pull/337))
- feat(console): redesign part 1: design spec, UI primitives and sidebar shell ([#331](https://github.com/BeastxD7/superaide/pull/331))
- feat(chat): page /app/messages by the standard cursor, skeleton, event index (0077) ([#335](https://github.com/BeastxD7/superaide/pull/335))
- feat(web): usePaginated, skeleton primitives, ListFooter and windowing ([#334](https://github.com/BeastxD7/superaide/pull/334))
- feat(marketplace): creator payout ledger with clawback, refund bond and a null settlement provider (no money movement) ([#329](https://github.com/BeastxD7/superaide/pull/329))
- feat(api): keyset pagination helper and ADR 0014 (audit and standard) ([#332](https://github.com/BeastxD7/superaide/pull/332))
- feat(marketplace): hash-chained skill receipt log, computed tiers, a stdlib recompute script and a bucketed trust claim ([#324](https://github.com/BeastxD7/superaide/pull/324))
- feat(marketplace): blast-radius cooldown for new skill versions and reserved names of retired skills ([#309](https://github.com/BeastxD7/superaide/pull/309))
- feat(marketplace): measured skill trust signals: scanner disagreement ledger, runtime behaviour card, demotion on a manifest gap ([#319](https://github.com/BeastxD7/superaide/pull/319))
- feat(brain): unlisted, expiring, revocable share link for the Glass brain (#55) ([#274](https://github.com/BeastxD7/superaide/pull/274))
- feat(brain): lock a fact so nothing automatic merges, retires or fades it (#55) ([#273](https://github.com/BeastxD7/superaide/pull/273))
- feat(tooltrace): replay before update (#110) ([#253](https://github.com/BeastxD7/superaide/pull/253))
- feat(identity): unknown account named like a contact is a stranger; ask the owner (#93) ([#251](https://github.com/BeastxD7/superaide/pull/251))
- feat(slack): link the owner's Slack account with a code made in the app ([#275](https://github.com/BeastxD7/superaide/pull/275))
- feat(voice): app-only confirmation card, realtime provider interface, 30-task text-vs-voice experiment (#125) ([#271](https://github.com/BeastxD7/superaide/pull/271))
- feat(school): signed lesson files and owner-chosen categories for sharing beyond this instance (#57) ([#264](https://github.com/BeastxD7/superaide/pull/264))
- feat(import): Lobster Rescue audit, import and five-volunteer measurement kit (#141) ([#270](https://github.com/BeastxD7/superaide/pull/270))
- feat(privacy): Airlock on by default after fixing the misses found on realistic conversations (#128) ([#261](https://github.com/BeastxD7/superaide/pull/261))
- feat(tooltrace): model-swap drill (#110) ([#255](https://github.com/BeastxD7/superaide/pull/255))
- feat(memory): owner-only reading view of episode summaries (#50) ([#269](https://github.com/BeastxD7/superaide/pull/269))
- feat: share.sh, run the latest app on this Mac and share it with a Cloudflare tunnel ([#259](https://github.com/BeastxD7/superaide/pull/259))
- feat(brain): struck-through replaced facts and a free-floating layout (#55) ([#268](https://github.com/BeastxD7/superaide/pull/268))
- feat(web): nonce-based Content-Security-Policy ([#260](https://github.com/BeastxD7/superaide/pull/260))
- feat(memory): sleep job merges near-duplicate and contradicting notes (#50) ([#262](https://github.com/BeastxD7/superaide/pull/262))
- feat(card): Principal Card on Telegram and Slack, cards sent, per-card revoke, owner page (#135) ([#263](https://github.com/BeastxD7/superaide/pull/263))
- feat(mandate): narrowing keys, delegation chain, cascade revoke and an owner list (#112) ([#256](https://github.com/BeastxD7/superaide/pull/256))
- feat(approvals): APPROVAL_BINDING defaults to strict (#111) ([#257](https://github.com/BeastxD7/superaide/pull/257))
- feat(tooltrace): audition candidate skills on the last tasks (#110) ([#254](https://github.com/BeastxD7/superaide/pull/254))
- feat(web): show and choose the language the call listens in ([#250](https://github.com/BeastxD7/superaide/pull/250))
- feat(triage): kNN head shows its shadow status and promotion gate (#117) ([#252](https://github.com/BeastxD7/superaide/pull/252))
- feat(memory): short facts from calendar reads and verified mail, behind a flag (off) ([#246](https://github.com/BeastxD7/superaide/pull/246))
- feat(insights): The Mirror, a weekly note of patterns with receipts, behind MIRROR_ENABLED (#54) ([#205](https://github.com/BeastxD7/superaide/pull/205))
- feat(journal): Ava keeps score, an opt-in decision journal with calibration (#53) ([#204](https://github.com/BeastxD7/superaide/pull/204))
- feat(mail): MAIL_LOG_BODY to keep links out of the log; e2e output readable ([#239](https://github.com/BeastxD7/superaide/pull/239))
- feat(memory): Memory passport, an open export and import of what Ava knows (#56) ([#202](https://github.com/BeastxD7/superaide/pull/202))
- feat(memory): real erasure of a source, a person's data, optional roll-off (#48) ([#189](https://github.com/BeastxD7/superaide/pull/189))
- feat(api): one error envelope and a request id on every response (ADR 0013, phase 1) ([#234](https://github.com/BeastxD7/superaide/pull/234))
- feat(memory): document chunks, hybrid search over passages, cited passages (#47) ([#188](https://github.com/BeastxD7/superaide/pull/188))
- feat(policy): quote and odds on the approval card (#139) ([#209](https://github.com/BeastxD7/superaide/pull/209))
- feat(school): local consent-gated lesson export and import (#57), flag off ([#226](https://github.com/BeastxD7/superaide/pull/226))
- feat(triage): deletable kNN urgency head and Wilson-bound promotion view (#117), flag off ([#225](https://github.com/BeastxD7/superaide/pull/225))
- feat(coach): Rehearsal room, practise a hard conversation (#58), flag off ([#224](https://github.com/BeastxD7/superaide/pull/224))
- feat(rules): time-travel standing orders, replayed against history first (#52) ([#221](https://github.com/BeastxD7/superaide/pull/221))
- feat(forknight): three read-only overnight drafts of a choice (#127) ([#220](https://github.com/BeastxD7/superaide/pull/220))
- feat(dissent): three-model dissent desk with a non-LLM classifier (#126) ([#219](https://github.com/BeastxD7/superaide/pull/219))
- feat(memsocket): read-only MCP endpoint over audience-gated memory (#138) ([#218](https://github.com/BeastxD7/superaide/pull/218))
- feat(privacy): Airlock, redact other people's data before anything is uploaded to a model (#128) ([#215](https://github.com/BeastxD7/superaide/pull/215))
- feat(approvals): bind every approval kind to its exact payload, expiry, strict-readiness report (#111) ([#195](https://github.com/BeastxD7/superaide/pull/195))
- feat(notebook): Glass brain, a visual map of what Ava knows (#55) ([#203](https://github.com/BeastxD7/superaide/pull/203))
- feat(principal): signed principal card page for outbound email (#135) ([#210](https://github.com/BeastxD7/superaide/pull/210))
- feat(import): import lease, third-party exports become small cards under an erasable lease (#133) ([#214](https://github.com/BeastxD7/superaide/pull/214))
- feat(web): document-processing loader and retry in the introduction ([#222](https://github.com/BeastxD7/superaide/pull/222))
- feat(memory): episodes and a nightly sleep job with guardrails (#50) ([#194](https://github.com/BeastxD7/superaide/pull/194))
- feat(policy): footprint guard (#136) ([#208](https://github.com/BeastxD7/superaide/pull/208))
- feat(voice): two-channel voice, a call's proposals are confirmed in text, never by voice (#125) ([#216](https://github.com/BeastxD7/superaide/pull/216))
- feat(db): start-up check that the database is at the migration the code expects ([#211](https://github.com/BeastxD7/superaide/pull/211))
- feat(memory): trust rank and supersession, a weaker source never overwrites the owner (#46) ([#190](https://github.com/BeastxD7/superaide/pull/190))
- feat(memory): importance, decay only for time-bound facts, spaced re-checks (#49) ([#193](https://github.com/BeastxD7/superaide/pull/193))
- feat(learning): Overnight Tuner, suggest only (#118) ([#180](https://github.com/BeastxD7/superaide/pull/180))
- feat(skills): Skill Ratchet, only owner-edited playbooks go live (#120) ([#179](https://github.com/BeastxD7/superaide/pull/179))
- feat(trust): relationship safety and a steady voice (#124) ([#178](https://github.com/BeastxD7/superaide/pull/178))

**Changed**
- Approval outcomes in the conversation, list_approvals, pushed back to the channel (#340) ([#363](https://github.com/BeastxD7/superaide/pull/363))
- Voice: no approval card for a spoken request nothing can carry out (#326) ([#365](https://github.com/BeastxD7/superaide/pull/365))
- gmail_compose: new emails to an address the owner typed, approval first (#339) ([#360](https://github.com/BeastxD7/superaide/pull/360))
- Where things stand: approvals from the database in every owner prompt (#340) ([#359](https://github.com/BeastxD7/superaide/pull/359))
- Capability facts from code and no unbacked action claims (#328) ([#352](https://github.com/BeastxD7/superaide/pull/352))
- Read the history before asking: no approvals for mail that would not reach a person (#327) ([#350](https://github.com/BeastxD7/superaide/pull/350))
- Tell the model what Airlock placeholders mean (#338) ([#351](https://github.com/BeastxD7/superaide/pull/351))
- Approvals: refuse what nothing can carry out, say what yes does (#326) ([#343](https://github.com/BeastxD7/superaide/pull/343))
- evals(memory): end-to-end file and link ingestion scenarios (#51) ([#266](https://github.com/BeastxD7/superaide/pull/266))
- evals(memory): opt-in Mem0 baseline as an optional extra (#51) ([#267](https://github.com/BeastxD7/superaide/pull/267))
- deploy: PWA on in production builds; document TRUST_PROXY_HEADERS behind Caddy ([#247](https://github.com/BeastxD7/superaide/pull/247))

**Fixed**
- fix(halt): resume Ava from the app, make the console Resume lift a halt, drop the false promise ([#306](https://github.com/BeastxD7/superaide/pull/306))
- fix(ui): make [n] citation marks accessible, clickable sources (#341) ([#344](https://github.com/BeastxD7/superaide/pull/344))
- fix(app): Capabilities says when Google is disconnected ([#311](https://github.com/BeastxD7/superaide/pull/311))
- fix(notebook): show a file saved but not read yet, with a way to erase it; refresh after a failed forget ([#316](https://github.com/BeastxD7/superaide/pull/316))
- fix(share): first update step also works on a detached HEAD ([#322](https://github.com/BeastxD7/superaide/pull/322))
- fix(approvals): expired requests are not offered or counted; approvals refresh live with a sidebar badge ([#308](https://github.com/BeastxD7/superaide/pull/308))
- fix(chat): reach messages older than the newest 200 instead of silently dropping them ([#318](https://github.com/BeastxD7/superaide/pull/318))
- fix(web): a tab notices that the browser now belongs to someone else ([#325](https://github.com/BeastxD7/superaide/pull/325))
- fix(people): Home counts what it hides, People shows Recognised on on a phone, intro closing line counts people once ([#323](https://github.com/BeastxD7/superaide/pull/323))
- fix(app): Home's channel rows follow the connected bot/app ([#310](https://github.com/BeastxD7/superaide/pull/310))
- fix(google): Calendar and Gmail connect return to the address the owner is using ([#315](https://github.com/BeastxD7/superaide/pull/315))
- fix(reminders): Home no longer lists a sent reminder as coming up; a stale reminder leaves the list after its error ([#312](https://github.com/BeastxD7/superaide/pull/312))
- fix(console): one operator visit is one access-log entry; the owner's own sign-in can't be unlinked ([#307](https://github.com/BeastxD7/superaide/pull/307))
- fix(web): one shared agent state, refreshed on focus, poll and after changes ([#304](https://github.com/BeastxD7/superaide/pull/304))
- fix(console): show app-saved connections, count only live notes, no false identity failure ([#303](https://github.com/BeastxD7/superaide/pull/303))
- fix(web): moving focus into the next introduction step must not scroll the panel ([#258](https://github.com/BeastxD7/superaide/pull/258))
- fix(tenancy): concurrent first hires no longer race on the default tenant ([#238](https://github.com/BeastxD7/superaide/pull/238))
- fix(google): tell the owner when the Calendar API is off or a permission is missing, and don't retry that 403 ([#236](https://github.com/BeastxD7/superaide/pull/236))
- fix(google): say when the Gmail/Calendar API isn't enabled, and log why a Google sign-in failed ([#227](https://github.com/BeastxD7/superaide/pull/227))
- fix(knowledge): a document whose facts can't be saved is failed, told to the owner and retryable ([#213](https://github.com/BeastxD7/superaide/pull/213))
- fix(intro): "I connected my own key" needs a key this instance saved ([#212](https://github.com/BeastxD7/superaide/pull/212))
- fix(db): duplicate migration revision 0039 on main (memory_importance becomes 0040) ([#206](https://github.com/BeastxD7/superaide/pull/206))
- fix(web): visible focus rings on sign-in and introduction fields ([#185](https://github.com/BeastxD7/superaide/pull/185))
- fix(web): account menu on Radix DropdownMenu ([#184](https://github.com/BeastxD7/superaide/pull/184))

**Tests**
- test(e2e): fix the flaky approvals 'No' spec that is red on main ([#357](https://github.com/BeastxD7/superaide/pull/357))
- test: the skeleton geometry check reads the real bubble once it has settled ([#347](https://github.com/BeastxD7/superaide/pull/347))
- test: secret markers must be non-hex so random ids can't contain them (flaky isolation tests) ([#321](https://github.com/BeastxD7/superaide/pull/321))
- test: fix the Needs-you polling e2e that red-lights every PR since #304 ([#320](https://github.com/BeastxD7/superaide/pull/320))
- test(web): unit tests for chat reducer, audit wording, speech helpers; e2e for console race and long text (#81) ([#265](https://github.com/BeastxD7/superaide/pull/265))
- test: backend tests in parallel with pytest-xdist (PR 3/4) ([#237](https://github.com/BeastxD7/superaide/pull/237))
- test(jobs): the stop-between-jobs test no longer assumes which job is claimed first ([#223](https://github.com/BeastxD7/superaide/pull/223))
- test(evals): long-term-memory eval set and an offline regression gate (#51) ([#192](https://github.com/BeastxD7/superaide/pull/192))
- test(web): axe on more pages; focusable conversation scroller in the introduction ([#191](https://github.com/BeastxD7/superaide/pull/191))

**Documentation**
- docs(research): cross-channel, multi-party memory design and its research notes ([#313](https://github.com/BeastxD7/superaide/pull/313))
- docs(config): ENABLE_SIMULATOR off in the example, README explains local enable ([#245](https://github.com/BeastxD7/superaide/pull/245))
- docs(config): every environment variable documented, flags indexed in the README ([#240](https://github.com/BeastxD7/superaide/pull/240))
- docs(readme): new README with banner, brand kit and screenshots ([#228](https://github.com/BeastxD7/superaide/pull/228))

### 2026-10-22

**Added**
- feat(memory): every owner input reaches long-term memory with its channel (#14) ([#181](https://github.com/BeastxD7/superaide/pull/181))
- feat(trust): Owner Twin (#119) ([#169](https://github.com/BeastxD7/superaide/pull/169))
- feat(honesty): memory-free second opinion and measured agreement rate (#123) ([#177](https://github.com/BeastxD7/superaide/pull/177))
- feat(memory): "Why did you say that?" ablation replay (#122) ([#175](https://github.com/BeastxD7/superaide/pull/175))
- feat(learning): One Good Question (#121), stacked on #166 ([#168](https://github.com/BeastxD7/superaide/pull/168))
- feat(memory): Echo Filter, count independent origins (#137) ([#166](https://github.com/BeastxD7/superaide/pull/166))
- feat(reception): Ava talks with people she doesn't know, on the owner's behalf (ADR 0010 TR5) ([#153](https://github.com/BeastxD7/superaide/pull/153))
- feat(policy): circuit breaker and halt word (#140) ([#162](https://github.com/BeastxD7/superaide/pull/162))
- feat(approvals): Rubber-Stamp Brake (#134) ([#167](https://github.com/BeastxD7/superaide/pull/167))
- feat(calendar): push updates from Google Calendar (events.watch), behind GOOGLE_CALENDAR_PUSH ([#154](https://github.com/BeastxD7/superaide/pull/154))
- feat(calendar): free/busy and calendar actions (add, move, decline) with consent and approvals ([#147](https://github.com/BeastxD7/superaide/pull/147))
- feat(onboarding): set up Ava's brain first, then read files and links live ([#155](https://github.com/BeastxD7/superaide/pull/155))
- feat(approvals): bind a Gmail send approval to the content the owner saw (#111) ([#156](https://github.com/BeastxD7/superaide/pull/156))
- feat(gmail): inbox triage, the digest's email line, and Gmail settings in Capabilities ([#146](https://github.com/BeastxD7/superaide/pull/146))
- feat(voice): hands-free voice introduction call behind VOICE_INTRO_V2 (ADR 0012) ([#108](https://github.com/BeastxD7/superaide/pull/108))

**Fixed**
- fix(telegram): a slow turn no longer blocks polling or button presses (BE-15, #91) ([#183](https://github.com/BeastxD7/superaide/pull/183))
- fix(pipeline): a failing model phase, receptionist or send keeps the message and answers (#83) ([#182](https://github.com/BeastxD7/superaide/pull/182))
- fix(web): ARIA tabs, focus into next step, 16px fields on phones ([#176](https://github.com/BeastxD7/superaide/pull/176))
- fix(web): introduction load failure, IME Enter, one upload at a time ([#173](https://github.com/BeastxD7/superaide/pull/173))
- fix(brain): proactive notes that quote outsiders are not replayed to the model as Ava's turns (#90) ([#171](https://github.com/BeastxD7/superaide/pull/171))
- fix: refuse an unsafe public self-hosted start (#82); close delivered reminders after a grace period (#85) ([#163](https://github.com/BeastxD7/superaide/pull/163))
- fix(identity): a lost first-message insert race no longer drops the message (BE-14, #91) ([#164](https://github.com/BeastxD7/superaide/pull/164))
- fix(web): form validation, confirmations, stale approvals, redirects, dates, initials, dev verify link (#70 #77 #79 #97) ([#148](https://github.com/BeastxD7/superaide/pull/148))
- fix(jobs): recurring chains survive failures, stale claims and orphaned spend are recovered, turns can't starve the pool (#84 #86 #91) ([#158](https://github.com/BeastxD7/superaide/pull/158))
- fix(security): prompt contents, link-code names, handle probing, token logging (#93) ([#160](https://github.com/BeastxD7/superaide/pull/160))
- fix(web): contrast, 16px inputs, long-text wrapping, Needs-you toggles, focus, axe checks (#78) ([#151](https://github.com/BeastxD7/superaide/pull/151))
- fix(knowledge): a document removed while Ava reads it leaves no live facts (#87) ([#159](https://github.com/BeastxD7/superaide/pull/159))
- fix(brain): a rejected AI key is permanent for the run, visible to the owner, and cleared by a working one (#150) ([#157](https://github.com/BeastxD7/superaide/pull/157))
- fix(models): credits can no longer be priced at zero, budget messages and limits (#59 to #68) ([#102](https://github.com/BeastxD7/superaide/pull/102))
- fix: show errors and retries when the dashboard, Connections and hire page fail to load or act (#23 #35 #38-#43) ([#149](https://github.com/BeastxD7/superaide/pull/149))
- fix(web): chat stream cut, IME Enter, live region, Needs-you recovery, console conversation race (#74 #76) ([#144](https://github.com/BeastxD7/superaide/pull/144))
- fix(asklink): a suggested handle always passes the handle rules ([#143](https://github.com/BeastxD7/superaide/pull/143))
- fix(auth): a new account belongs to no Ava until it hires (#24 #25) ([#103](https://github.com/BeastxD7/superaide/pull/103))
- fix(web): tell "couldn't load" from "not hired" and "signed out"; handle a session that ends ([#99](https://github.com/BeastxD7/superaide/pull/99))
- fix(web): hero orb label, phone band and sidebar sheet, emoji initials (#36, #37, #32, #33) ([#96](https://github.com/BeastxD7/superaide/pull/96))
- fix: backend reliability (hosted mode in prod, reminders list, max_tokens, upload, small date and config bugs) ([#104](https://github.com/BeastxD7/superaide/pull/104))
- fix(auth): email change, Google sign-in and reset-link fixes (#18 #20 #26 #27 #28 #29 #30 #31) ([#101](https://github.com/BeastxD7/superaide/pull/101))
- fix(knowledge): read shared documents when the brain becomes ready, by any path ([#95](https://github.com/BeastxD7/superaide/pull/95))

**Tests**
- test(web): Vitest unit tests for the pure frontend logic ([#174](https://github.com/BeastxD7/superaide/pull/174))
- test(e2e): isolation test checks A's secret words, not digits a random id can contain ([#161](https://github.com/BeastxD7/superaide/pull/161))
- test(web): API proxy contract, reply safety, security headers (#81) ([#152](https://github.com/BeastxD7/superaide/pull/152))
- test(e2e): the long-link chat test no longer depends on reply timing ([#145](https://github.com/BeastxD7/superaide/pull/145))
- test(digest): the morning digest tests no longer depend on the time of day ([#107](https://github.com/BeastxD7/superaide/pull/107))
- test(api): guard that a streamed chat turn stays in its owner's instance (#94) ([#106](https://github.com/BeastxD7/superaide/pull/106))
- test(triage): make the catch-up tests independent of the time of day ([#100](https://github.com/BeastxD7/superaide/pull/100))

**Changed**
- One place to connect an AI model: remove the Connections Claude route ([#172](https://github.com/BeastxD7/superaide/pull/172))
- Remove the Claude card from Connections; point to the AI model page ([#165](https://github.com/BeastxD7/superaide/pull/165))
- Frontend hardening: no remote images, CSP and HSTS, error screens, console fixes ([#105](https://github.com/BeastxD7/superaide/pull/105))

**Documentation**
- docs: add a CHANGELOG of every merged pull request, its generator and release-note groups ([#170](https://github.com/BeastxD7/superaide/pull/170))
- docs(research): AI era research reports on learning, protocols and interaction ([#109](https://github.com/BeastxD7/superaide/pull/109))

**Dependencies**
- build(deps): Bump py-vapid from 1.9.4 to 1.9.6 in /backend in the python-minor-and-patch group ([#17](https://github.com/BeastxD7/superaide/pull/17))
- build(deps): Bump docker/build-push-action from 6 to 7 ([#16](https://github.com/BeastxD7/superaide/pull/16))
- build(deps): Bump gitleaks/gitleaks-action from 2 to 3 ([#15](https://github.com/BeastxD7/superaide/pull/15))
- build(deps): Bump the npm-minor-and-patch group across 2 directories with 2 updates ([#10](https://github.com/BeastxD7/superaide/pull/10))

**Maintenance**
- chore(github): consistent issue forms, PR template, labels and community files ([#11](https://github.com/BeastxD7/superaide/pull/11))

