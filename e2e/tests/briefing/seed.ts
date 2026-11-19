// A stranger's message the receptionist took, written straight to the table (the offline model cannot take one itself).
import { query } from '../../src/fixtures'

export async function takenMessage(text: string, opts: { topic?: string; rank?: string } = {}) {
  await query(
    `INSERT INTO reception_message (id, tenant_id, agent_id, identity_id, context_id, provider, handle, name, contact, topic, message,
        deadline_text, deadline_parse, claimed_urgency, rank, rule_hits, source, status)
     SELECT gen_random_uuid(), i.tenant_id, i.agent_id, i.id, c.id, 'telegram', '', 'Meera Iyer', '', $2, $1,
        '', '', 'normal', $3, '{}', 'model', 'new'
     FROM channel_identity i JOIN context c ON c.provider = i.provider AND c.agent_id = i.agent_id
     WHERE i.provider = 'telegram' AND i.person_id IS NULL AND c.kind = 'dm' LIMIT 1`,
    [text, opts.topic ?? 'the project', opts.rank ?? 'normal'],
  )
}

// The owner last wrote six hours ago.
export async function away() {
  await query(`UPDATE event SET created_at = now() - interval '6 hours' WHERE resolved_person_id IS NOT NULL`)
}
