import { query } from '../db'

export type SeededContact = { contactId: string; personId: string }

/** A person on the owner's list, with a Telegram account and one fact about them (fictional: Meera Iyer). */
export async function seedMeera(ownerPersonId: string): Promise<SeededContact> {
  const [row] = await query<{ id: string; person_id: string }>(
    `WITH p AS (
       INSERT INTO person (id, tenant_id, agent_id, created_at, display_name, kind, relation, status, channel_prefs, notice_basis)
       SELECT gen_random_uuid(), o.tenant_id, o.agent_id, now(), 'Meera Iyer', 'human', 'external', 'active', '{}', '{}'
       FROM person o WHERE o.id = $1::uuid RETURNING id, tenant_id, agent_id),
     c AS (
       INSERT INTO contact (id, tenant_id, agent_id, created_at, owner_person_id, person_id, relationship, vip, notes)
       SELECT gen_random_uuid(), p.tenant_id, p.agent_id, now(), $1::uuid, p.id, 'friend', false, '' FROM p RETURNING id, person_id),
     i AS (
       INSERT INTO channel_identity (id, tenant_id, agent_id, created_at, person_id, provider, tenant_scope, stable_key, aux_handles,
                                     trust_level, verification, lifecycle, risk_flags, endpoint_key)
       SELECT gen_random_uuid(), p.tenant_id, p.agent_id, now(), p.id, 'telegram', '', '4242', '{"username": "meera_i"}',
              2, '{}', 'active', '{}', '' FROM p RETURNING id),
     m AS (
       INSERT INTO memory_item (id, tenant_id, agent_id, created_at, content, subject_person_ids, source_event_ids, allowed_kind,
                                allowed_person_ids, min_trust, category, source, source_document_ids)
       SELECT gen_random_uuid(), p.tenant_id, p.agent_id, now(), 'Meera prefers calls after 6pm.', ARRAY[p.id], '{}', 'persons',
              ARRAY[$1::uuid], 2, 'person', 'telegram', '{}' FROM p RETURNING id)
     SELECT c.id, c.person_id FROM c`,
    [ownerPersonId],
  )
  return { contactId: row.id, personId: row.person_id }
}
