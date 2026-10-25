import { Diagram } from './lib.mjs'

const d = new Diagram({
  name: '10-data-security',
  title: 'Data model, tenancy and security boundaries',
  subtitle: 'Postgres is the only store. Every per-owner row carries tenant_id and agent_id and is fenced by Row-Level Security; secrets are encrypted outside the database\'s reach.',
  height: 1120,
})
d.zone('db', { x: 40, y: 120, w: 1150, h: 560, label: 'Postgres 17 + pgvector: tables by area', color: 'amber', icon: 'brand:postgresql' })
d.zone('rls', { x: 1230, y: 120, w: 650, h: 560, label: 'Isolation', color: 'rose', icon: 'lucide:shield-check' })
d.zone('tb', { x: 40, y: 710, w: 1840, h: 320, label: 'Trust boundaries', color: 'violet', icon: 'lucide:fingerprint-pattern' })

const t = (id, x, y, title, sub, icon) => d.node(id, { x, y, w: 360, h: 100, icon, title, sub, color: 'amber' })
t('t1', 62, 170, 'Accounts', 'user_account, user_session (token hashes only), auth_event, agent_hire', 'lucide:user')
t('t2', 442, 170, 'Identity', 'person, channel_identity, channel_invite, link_challenge, identity_claim, identity_link_event', 'lucide:fingerprint-pattern')
t('t3', 822, 170, 'Conversations', 'context, event (audience_person_ids, audience_has_unknown), episode', 'lucide:message-square')
t('t4', 62, 300, 'Knowledge', 'memory_item, memory_embedding, document, document_chunk, contact, owner_preference', 'lucide:brain')
t('t5', 442, 300, 'Approvals and policy', 'approval (bound_hash, expires_at), mandate, agent_state (stage, frozen, settings)', 'lucide:gavel')
t('t6', 822, 300, 'Reception and triage', 'reception_message, reception_settings, triage decisions and escalations', 'lucide:inbox')
t('t7', 62, 430, 'Jobs', 'job queue with dedupe keys, claimed with SKIP LOCKED, retried with back-off', 'lucide:timer')
t('t8', 442, 430, 'Money and audit', 'spend_ledger, audit_log, action_log (hash chain), tool_trace (hashes only)', 'lucide:coins')
t('t9', 822, 430, 'Secrets', 'integration_secret: AES-256-GCM ciphertext; the slot and instance are authenticated data', 'lucide:key-round')
d.note(62, 560, 1100, ['Alembic keeps the schema in step with the models (CI runs alembic check and a downgrade round trip). A new per-owner table must carry tenant_id and agent_id and be added to the RLS list; a test fails otherwise. Platform lookup tables (short card links, public handles) are exempt.'], { color: 'amber' })

d.node('r1', { x: 1252, y: 170, w: 606, h: 100, icon: 'lucide:layers', title: 'tenant_id and agent_id on every row', sub: 'Fixed in the single-owner MVP, but real keys for many owners: anyone can hire their own isolated Ava (ADR 0009)', color: 'rose' })
d.node('r2', { x: 1252, y: 300, w: 606, h: 110, icon: 'lucide:shield-check', title: 'Row-Level Security, FORCE enabled', sub: 'Policy instance_isolation compares agent_id with the transaction setting app.agent_id. No setting means no rows: it fails closed', color: 'rose', accent: '#dc2626' })
d.node('r3', { x: 1252, y: 440, w: 606, h: 100, icon: 'lucide:user', title: 'The app runs as role superaide_app', sub: 'No superuser, no BYPASSRLS. Migrations run as the schema owner (MIGRATION_DATABASE_URL)', color: 'rose' })
d.node('r4', { x: 1252, y: 570, w: 606, h: 90, icon: 'lucide:key-round', title: 'Master key outside the database', sub: 'Env, key file, OS keychain or ~/.config/superaide/master.key; derives the mandate and card keys too', color: 'rose' })
d.edge('r1', 'r2', { from: 'b', to: 't' }); d.edge('r2', 'r3', { from: 'b', to: 't' })

const b = (i, id, icon, title, sub, color = 'violet') => d.node(id, { x: 62 + i * 365, y: 760, w: 345, h: 150, icon, title, sub, color })
b(0, 'b1', 'lucide:monitor', 'Browser to Next.js', 'Same-origin cookie; httpOnly, SameSite=Lax; X-Requested-With on every change; CSP nonce per request')
b(1, 'b2', 'lucide:webhook', 'Internet to API', 'Telegram secret or Slack signature checked first; unverified is dropped; the proxy drops client X-Forwarded-For')
b(2, 'b3', 'lucide:scan-eye', 'API to model', 'Airlock swaps others\' contact details for placeholders; spend reserved first; web results taint the turn')
b(3, 'b4', 'lucide:terminal', 'Operator console', 'Admin token; owner content needs an X-Operator-Reason header and each visit is logged in the owner\'s audit')
b(4, 'b5', 'lucide:file-text', 'Protected surface', 'Audience gate, RLS and triage policy are pinned by a lock file and CODEOWNERS; the charter and skills are the editable surface')
d.note(62, 930, 1796, ['Threats considered: impostors (a name is never a credential), prompt injection through mail or web pages (tainted turns cannot skip approvals), cross-owner leakage (RLS plus the audience filter), key theft (ciphertext only in the database), runaway cost (hard caps), and a compromised model (it can only propose).'], { title: 'Threat model summary', color: 'rose' })
d.legend()
export default d
