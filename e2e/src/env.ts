// One isolated stack per E2E_SLOT: its own API port, web port and database, so
// several runs (or several test agents) can work side by side on one machine.
//   slot 0 -> API :8100, web :3100, database superaide_e2e_0
//   slot 1 -> API :8101, web :3101, database superaide_e2e_1 …
export const SLOT = Number(process.env.E2E_SLOT ?? 0)
if (!Number.isInteger(SLOT) || SLOT < 0 || SLOT > 99) throw new Error('E2E_SLOT must be a whole number from 0 to 99')

export const API_PORT = 8100 + SLOT
export const WEB_PORT = 3100 + SLOT
export const BASE_URL = process.env.E2E_BASE_URL ?? `http://localhost:${WEB_PORT}`
export const API_URL = `http://127.0.0.1:${API_PORT}`
// "Continue with Google" against a fake Google (scripts/fake-google.mjs). Muted by default: only with
// E2E_GOOGLE=1, which also starts the fake and switches Google sign-in on in the API.
export const GOOGLE = process.env.E2E_GOOGLE === '1'
export const FAKE_GOOGLE_PORT = 8300 + SLOT
export const FAKE_GOOGLE_URL = `http://127.0.0.1:${FAKE_GOOGLE_PORT}`

// A Postgres URL for a database we may create others from (CI and local defaults differ).
export const PG_ADMIN_URL = process.env.E2E_PG_ADMIN_URL ?? 'postgresql://agent:agent@localhost:5432/postgres'
export const DB_NAME = `superaide_e2e_${SLOT}`

function withDatabase(url: string, db: string): string {
  const u = new URL(url)
  u.pathname = `/${db}`
  return u.toString()
}

export const PG_URL = withDatabase(PG_ADMIN_URL, DB_NAME)
// SQLAlchemy wants the driver in the scheme.
export const DATABASE_URL = PG_URL.replace(/^postgres(ql)?:\/\//, 'postgresql+psycopg://')

export const ASK_LINK = process.env.E2E_ASK_LINK === '1'

export const ADMIN_TOKEN = 'e2e-admin-token'
export const PASSWORD = 'correct horse battery staple'

// Settings for the API under test: fake model (no spend), generous throttles so
// many sign-ups from one machine don't trip them, and a throwaway vault key.
export function apiEnv(): Record<string, string> {
  return {
    DATABASE_URL,
    FRONTEND_ORIGIN: `http://localhost:${WEB_PORT}`,
    FAKE_LLM: 'true',
    ANTHROPIC_API_KEY: '',
    TELEGRAM_BOT_TOKEN: '',
    SLACK_BOT_TOKEN: '',
    SLACK_APP_TOKEN: '',
    ADMIN_TOKEN,
    ENABLE_SIMULATOR: 'true',
    RUN_CHANNELS_IN_API: 'false',
    SIGNUPS_PER_IP_PER_HOUR: '100000',
    LOGIN_FAILURES_PER_IP: '100000',
    // A fixed throwaway vault key, so tests never touch your real keychain.
    SECRETS_MASTER_KEY: Buffer.alloc(32, SLOT + 1).toString('base64'),
    // The "Ask my Ava" link is off by default; E2E_ASK_LINK=1 turns it on for tests/ask-link.spec.ts.
    ...(ASK_LINK ? { ASK_LINK_ENABLED: 'true', ASK_MIN_SECONDS: '1' } : {}),
    ...(GOOGLE
      ? {
          GOOGLE_SIGNIN_ENABLED: 'true',
          GOOGLE_CLIENT_ID: 'e2e-client.apps.googleusercontent.com',
          GOOGLE_CLIENT_SECRET: 'e2e-not-a-secret',
          GOOGLE_FAKE_BASE_URL: FAKE_GOOGLE_URL,
          // The fake Google accepts any address; this one stands for a second address registered with Google.
          GOOGLE_REDIRECT_ORIGINS: `http://127.0.0.1:${WEB_PORT}`,
        }
      : {}),
  }
}
