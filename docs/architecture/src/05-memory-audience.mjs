import { Diagram } from './lib.mjs'

const d = new Diagram({
  name: '05-memory-audience',
  title: 'Memory architecture and audience model',
  subtitle: 'One notebook per owner, shared across web, Telegram and Slack DMs. Who may see a note is decided inside the SQL query, before the prompt is built.',
  height: 1250,
})
d.zone('w', { x: 40, y: 120, w: 520, h: 520, label: 'Writing', color: 'blue', icon: 'lucide:pen-line' })
d.zone('s', { x: 600, y: 120, w: 520, h: 520, label: 'Store: memory_item', color: 'amber', icon: 'brand:postgresql' })
d.zone('r', { x: 1160, y: 120, w: 720, h: 520, label: 'Reading (context/recall.py)', color: 'green', icon: 'lucide:search' })

d.node('w1', { x: 62, y: 170, w: 476, h: 76, icon: 'lucide:message-square', title: 'Owner messages (DM only)', sub: 'knowledge/learn.py extracts facts after the reply; "remember that ..." is stored at once', color: 'blue' })
d.node('w2', { x: 62, y: 266, w: 476, h: 76, icon: 'lucide:file-text', title: 'Files, links, calls', sub: 'knowledge/ingest.py: facts keep their source; removing a file removes its facts', color: 'blue' })
d.node('w3', { x: 62, y: 362, w: 476, h: 76, icon: 'brand:gmail', title: 'Mail and calendar facts', sub: 'Only if CONNECTOR_FACTS_ENABLED: up to 3 short facts, never raw text', color: 'blue' })
d.node('w4', { x: 62, y: 458, w: 476, h: 76, icon: 'lucide:pen-line', title: 'The owner, in the Notebook', sub: 'Correct (new row, old row ends), forget, or lock a fact', color: 'blue' })
d.node('w5', { x: 62, y: 554, w: 476, h: 66, icon: 'lucide:ghost', title: 'Strangers and other people', sub: 'Never write owner facts. Conflicts wait for the owner', color: 'rose', accent: '#dc2626' })

d.node('s1', { x: 622, y: 170, w: 476, h: 110, icon: 'lucide:eye', title: 'Audience columns', sub: 'allowed_kind: persons, org or public. allowed_person_ids. min_trust. Owner facts are persons = [owner] unless marked shareable (public)', color: 'amber' })
d.node('s2', { x: 622, y: 300, w: 476, h: 96, icon: 'lucide:badge-check', title: 'Provenance', sub: 'category, source, trust_rank (owner > document > link > third party > web), belief_state fact or hearsay', color: 'amber' })
d.node('s3', { x: 622, y: 416, w: 476, h: 96, icon: 'lucide:clock', title: 'Lifetime', sub: 'valid_from and valid_to, importance, time-bound decay (14 day half-life), locked, conflict_status', color: 'amber' })
d.node('s4', { x: 622, y: 532, w: 476, h: 90, icon: 'lucide:vector-square', title: 'memory_embedding (pgvector)', sub: 'One vector per fact and model; local embedder by default. Without pgvector, keyword search only', color: 'amber' })

d.node('r1', { x: 1182, y: 170, w: 676, h: 76, icon: 'lucide:text-search', title: 'Keyword arms', sub: 'Full-text AND, then OR (UK and US spelling, synonyms), plus trigram', color: 'green' })
d.node('r2', { x: 1182, y: 266, w: 676, h: 76, icon: 'lucide:binary', title: 'Meaning arm', sub: 'pgvector nearest neighbours in the same SELECT', color: 'green' })
d.node('r3', { x: 1182, y: 362, w: 676, h: 90, icon: 'lucide:filter', title: 'memory_filter(audience), inside every arm', sub: 'public always passes; org only if everyone present is internal; persons only if all present are allowed; unknown people present means public only. Expired and conflicting notes are excluded', color: 'green', accent: '#34a853' })
d.node('r4', { x: 1182, y: 472, w: 676, h: 76, icon: 'lucide:layers', title: 'Fuse and weigh', sub: 'Reciprocal rank fusion, times importance. Code also pre-recalls 4 notes before the model runs', color: 'green' })
d.node('r5', { x: 1182, y: 568, w: 676, h: 54, icon: 'lucide:brain', title: 'Visible memories go into the prompt', sub: '', color: 'green' })
for (const [a, b] of [['r1', 'r2'], ['r2', 'r3'], ['r3', 'r4'], ['r4', 'r5']]) d.edge(a, b, { from: 'b', to: 't', fa: 0.5, fb: 0.5 })
d.edge('s1', 'r3', { from: 'r', to: 'l', n: 1, label: 'SQL', via: [[1140, 225], [1140, 407]] })

d.text(40, 678, 'What each conversation can reach', { size: 17, bold: true, color: '#0f172a' })
const Y = { t: 'Yes', bg: '#dcfce7', fg: '#166534', bold: true }
const N = { t: 'No', bg: '#fee2e2', fg: '#991b1b', bold: true }
const P = { t: 'Public only', bg: '#fef9c3', fg: '#854d0e', bold: true }
d.table(40, 700, [
  { w: 400, label: 'Conversation' }, { w: 230, label: 'Owner private notes' }, { w: 230, label: 'Public-level notes' }, { w: 250, label: 'Owner\'s other-channel DMs (48 h)' }, { w: 230, label: 'Other people\'s DMs' }, { w: 230, label: 'Learns from the turn' }, { w: 270, label: 'Tools' },
], [
  ['Owner DM on web, Telegram or Slack', Y, Y, Y, N, Y, { t: 'All', bg: '#dcfce7', fg: '#166534', bold: true }],
  ['Owner mentions Ava in a shared Slack channel', N, Y, N, N, { t: 'No', bg: '#fee2e2', fg: '#991b1b', bold: true }, { t: '5 tools', bg: '#fef9c3', fg: '#854d0e', bold: true }],
  ['A colleague in a DM', N, { t: 'Org and public', bg: '#fef9c3', fg: '#854d0e', bold: true }, N, N, N, { t: 'Proposals only', bg: '#fef9c3', fg: '#854d0e', bold: true }],
  ['A stranger like Meera, with the receptionist', N, Y, N, N, N, { t: 'None', bg: '#fee2e2', fg: '#991b1b', bold: true }],
  ['A stranger, receptionist off', N, N, N, N, N, { t: 'None, no model', bg: '#fee2e2', fg: '#991b1b', bold: true }],
], { rowH: 42 })
d.note(40, 1000, 900, ['Sleep job (SLEEP_JOB_ENABLED, off by default): nightly it cuts the day into episodes and merges exact and near-duplicate notes. The small model only judges pairs; code decides what is retired, at most a quarter a night, with a pre-image saved so it can be undone.'], { title: 'Maintenance', color: 'violet' })
d.note(980, 1000, 900, ['Erase: deleting a document deletes its chunks and the facts only it supported; erasing a third party removes notes about them and empties their message text. Echo filter (off by default): facts heard via links, Telegram or Slack stay hearsay until a second independent origin or the owner confirms.'], { title: 'Forgetting and doubt', color: 'rose' })
d.legend()
export default d
