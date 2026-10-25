import { Diagram } from './lib.mjs'

const d = new Diagram({
  name: '06-onboarding-flow',
  title: 'Onboarding: from sign-up to a working assistant',
  subtitle: 'Account and hire first, then a conversational introduction at /meet. Typed answers become settings; facts become notes with a source.',
  height: 1130,
})
d.zone('acct', { x: 40, y: 120, w: 1840, h: 250, label: 'Account and hire (auth/, app/catalog.py)', color: 'blue', icon: 'lucide:user-round' })
d.zone('intro', { x: 40, y: 400, w: 1840, h: 560, label: 'Introduction at /meet (app/onboarding.py, table onboarding)', color: 'green', icon: 'lucide:sparkles' })
const col = (i) => 62 + i * 364
const W = 330
d.node('a1', { x: col(0), y: 170, w: W, h: 170, icon: 'lucide:user', title: 'Sign up or log in', sub: 'Email and password (Argon2id), or Continue with Google when configured. httpOnly session cookie, CSRF header, lockouts, auth audit events', color: 'blue' })
d.node('a2', { x: col(1), y: 170, w: W, h: 170, icon: 'lucide:mail', title: 'Verify email', sub: 'Only when REQUIRE_VERIFIED_EMAIL is on and SMTP works. Otherwise skipped', color: 'blue' })
d.node('a3', { x: col(2), y: 170, w: W, h: 170, icon: 'lucide:user-check', title: 'Hire Ava', sub: 'Catalog page. Creates the agent_hire (one active hire per agent). Leo and Kai are shown as coming soon', color: 'blue' })
d.node('a4', { x: col(3), y: 170, w: W, h: 170, icon: 'lucide:brain', title: 'Choose a brain', sub: 'Superaide AI credits (if the operator enabled them), your own Claude key, or another provider. Keys are verified, then encrypted in the vault. Skipped if a model is already ready', color: 'blue' })
d.node('a5', { x: col(4), y: 170, w: W, h: 170, icon: 'lucide:hand', title: 'Hello', sub: 'Ava introduces herself. She starts at the Infant stage: nothing she does reaches anyone without approval', color: 'blue' })
for (let i = 1; i < 5; i++) d.edge('a' + i, 'a' + (i + 1), { n: i })

// snake: row 2 right to left, row 3 left to right
d.node('b1', { x: col(4), y: 450, w: W, h: 150, icon: 'lucide:book-open', title: 'About you', sub: 'By chat, by files and links, or on a video call. Facts keep their source (source = chat, call, document, link)', color: 'green' })
d.node('b2', { x: col(3), y: 450, w: W, h: 150, icon: 'lucide:users', title: 'People who matter', sub: 'Skippable. Written to contact', color: 'green' })
d.node('b3', { x: col(2), y: 450, w: W, h: 150, icon: 'lucide:siren', title: 'What is urgent', sub: 'owner_preference.urgent_topics; used by triage rules', color: 'green' })
d.node('b4', { x: col(1), y: 450, w: W, h: 150, icon: 'lucide:moon', title: 'How to reach you', sub: 'owner_preference: digest times, interrupt channel, quiet hours (default 22:00 to 07:00), VIPs break quiet, timezone', color: 'green' })
d.node('b5', { x: col(0), y: 450, w: W, h: 150, icon: 'lucide:eye', title: 'What may be shared', sub: 'Skippable. Ticks from availability, in a meeting, work email, company role, office city; plus a free-text never-share line', color: 'amber', accent: '#f2a900' })
d.edge('a5', 'b1', { from: 'b', to: 't' })
for (let i = 1; i < 5; i++) d.edge('b' + i, 'b' + (i + 1), { from: 'l', to: 'r' })
d.node('c1', { x: col(0), y: 740, w: W, h: 150, icon: 'lucide:gavel', title: 'Ground rules', sub: 'Your own standing rules for Ava, said in plain words', color: 'green' })
d.node('c2', { x: col(1), y: 740, w: W, h: 150, icon: 'lucide:webhook', title: 'Connect', sub: 'Telegram (bot token), Slack (app tokens), Google (Calendar and Gmail). Tokens go to the vault, checked first', color: 'green' })
d.node('c3', { x: col(2), y: 740, w: W, h: 150, icon: 'lucide:check', title: 'Done: dashboard', sub: 'Home, Chat, Approvals, Reminders, Notebook, People, Capabilities, Settings', color: 'green' })
d.edge('b5', 'c1', { from: 'b', to: 't' })
d.edge('c1', 'c2'); d.edge('c2', 'c3')
d.note(col(3), 720, 2 * W + 34, ['Stored in owner_preference.shareable (a list of the ticked labels) and owner_preference.never_share (text). The never-share line also becomes a locked note. The receptionist reads only the labels and the public profile; strangers never see the free text.'], { title: 'Where "what may be shared" lives', color: 'amber' })
d.note(col(3), 840, 2 * W + 34, ['Meera, who is not the owner, never goes through this flow. She only ever sees what these ticks make public.'], { title: 'Example', color: 'rose' })
d.legend()
export default d
