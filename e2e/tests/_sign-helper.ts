// Shared by the sign-off specs (not a spec itself).
import { query } from '../src/fixtures'

export const TENANT = '00000000-0000-4000-8000-000000000001'
export const AGENT = '00000000-0000-4000-8000-000000000002'

export async function queueEmail(): Promise<void> {
  await query(
    `INSERT INTO approval (id, tenant_id, agent_id, created_at, kind, payload, status)
     VALUES (gen_random_uuid(), $1, $2, now(), 'gmail_send', $3::jsonb, 'pending')`,
    [
      TENANT,
      AGENT,
      JSON.stringify({
        summary: 'To: <jane@startup.example> · Subject: Out tomorrow · Hi Jane, I will be out tomorrow.',
        to: ['jane@startup.example'],
        subject: 'Out tomorrow',
        draft_id: 'd-signed',
        content_hash: 'h-signed',
        alt_draft_id: 'd-plain',
        alt_content_hash: 'h-plain',
        signature_available: true,
        sign_as_ava: true,
        signature_id: 'abc123',
        signature_label: 'Sign as Ava, personal assistant of Jordan',
        signature_text: '--\nRegards,\nAva, personal assistant of Jordan\n\nAva is an AI assistant acting for Jordan.',
      }),
    ],
  )
}
