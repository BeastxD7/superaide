// Plant what the connect form would have stored, without calling the real service (Telegram, Slack…), and link the owner's own
// channel account. AES-256-GCM with the slot as associated data, like the vault (ADR 0009 T3).
import { createCipheriv, randomBytes } from 'node:crypto'
import { query } from '../db'
import { apiEnv } from '../env'

export async function plantSecret(provider: string, name: string, value: string): Promise<void> {
  const [scope] = await query<{ tenant_id: string; agent_id: string }>("SELECT tenant_id, agent_id FROM context WHERE external_id LIKE 'web:%' LIMIT 1")
  const key = Buffer.from(apiEnv().SECRETS_MASTER_KEY, 'base64')
  const nonce = randomBytes(12)
  const cipher = createCipheriv('aes-256-gcm', key, nonce)
  cipher.setAAD(Buffer.from(`${scope.agent_id}/${provider}/${name}`))
  const ct = Buffer.concat([cipher.update(value, 'utf8'), cipher.final(), cipher.getAuthTag()])
  const token = Buffer.concat([Buffer.from([1]), nonce, ct]).toString('base64')
  await query(
    `INSERT INTO integration_secret (id, tenant_id, agent_id, aad, provider, name, ciphertext, hint, meta, updated_at, created_at)
     VALUES (gen_random_uuid(), $1, $2, 'instance', $3, $4, $5, $6, '{}'::jsonb, now(), now())`,
    [scope.tenant_id, scope.agent_id, provider, name, token, `${value.slice(0, 5)}…${value.slice(-4)}`],
  )
}

export async function linkOwner(provider: string, key: string): Promise<void> {
  await query(
    `INSERT INTO channel_identity (id, tenant_id, agent_id, created_at, person_id, provider, tenant_scope, stable_key, aux_handles, trust_level, verification, lifecycle, risk_flags, endpoint_key)
     SELECT gen_random_uuid(), p.tenant_id, p.agent_id, now(), p.id, $1, '', $2, '{"display":"Ananya"}'::jsonb, 3, '{}'::jsonb, 'active', '{}', ''
     FROM person p JOIN user_account u ON u.person_id = p.id LIMIT 1`,
    [provider, key],
  )
}

// What the startup / connect check of the Slack scopes records (backend channels/slack/scopes.py), without calling Slack.
export async function plantMissingScopes(provider: string, missing: string[]): Promise<void> {
  const [scope] = await query<{ tenant_id: string; agent_id: string }>("SELECT tenant_id, agent_id FROM context WHERE external_id LIKE 'web:%' LIMIT 1")
  await query(
    `INSERT INTO agent_state (tenant_id, agent_id, key, value) VALUES ($1, $2, $3, $4::jsonb)
     ON CONFLICT (tenant_id, agent_id, key) DO UPDATE SET value = EXCLUDED.value`,
    [scope.tenant_id, scope.agent_id, `channel_scopes:${provider}`, JSON.stringify({ missing })],
  )
}

// A Slack channel Ava knows (someone mentioned her there), with `stored` room messages saved by reading it whole (#509).
export async function plantSlackChannel(channel: string, title: string, stored = 0): Promise<void> {
  const [scope] = await query<{ tenant_id: string; agent_id: string }>("SELECT tenant_id, agent_id FROM context WHERE external_id LIKE 'web:%' LIMIT 1")
  const [ctx] = await query<{ id: string }>(
    `INSERT INTO context (id, tenant_id, agent_id, created_at, provider, tenant_scope, external_id, kind, audience_class, title, last_event_at)
     VALUES (gen_random_uuid(), $1, $2, now(), 'slack', 'T_E2E', $3, 'group', 'team', $4, now()) RETURNING id`,
    [scope.tenant_id, scope.agent_id, channel, title],
  )
  for (let i = 0; i < stored; i++) {
    await query(
      `INSERT INTO event (id, tenant_id, agent_id, created_at, context_id, direction, trust_at_time, audience_person_ids, audience_has_unknown, content, meta, webhook_verified, external_id)
       VALUES (gen_random_uuid(), $1, $2, now(), $3, 'in', 0, '{}', true, $4, '{"ambient": true}'::jsonb, true, $5)`,
      [scope.tenant_id, scope.agent_id, ctx.id, `room message ${i + 1}`, `1710000000.00010${i}`],
    )
  }
}

export async function storedRoomMessages(channel: string): Promise<number> {
  const [row] = await query<{ n: string }>(
    `SELECT count(*) AS n FROM event e JOIN context c ON c.id = e.context_id WHERE c.external_id = $1 AND e.meta ? 'ambient'`,
    [channel],
  )
  return Number(row.n)
}
