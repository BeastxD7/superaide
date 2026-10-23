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
