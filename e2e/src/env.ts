// One isolated stack per E2E_SLOT: its own API port, web port and database, so
// several runs (or several test agents) can work side by side on one machine.
//   slot 0 -> API :8100, web :3100, database superaide_e2e_0
//   slot 1 -> API :8101, web :3101, database superaide_e2e_1 …
export const SLOT = Number(process.env.E2E_SLOT ?? 0)
if (!Number.isInteger(SLOT) || SLOT < 0 || SLOT > 99) throw new Error('E2E_SLOT must be a whole number from 0 to 99')

export const API_PORT = 8100 + SLOT
export const WEB_PORT = 3100 + SLOT
export const BASE_URL = process.env.E2E_BASE_URL ?? `http://localhost:${WEB_PORT}`
// Opt-in (E2E_PROXY=1): three `next start` servers in front of scripts/fake-upstream.mjs, for *.proxy.spec.ts.
//   A: defaults        B: TRUST_PROXY_HEADERS=true        C: the upstream is not there at all
export const PROXY = process.env.E2E_PROXY === '1'
export const UPSTREAM_PORT = 8400 + SLOT
export const PROXY_A = 3500 + SLOT
export const PROXY_B = 3600 + SLOT
export const PROXY_C = 3700 + SLOT
// Opt-in (E2E_DEV=1): also run `next dev` (React StrictMode double effects) for the *.dev.spec.ts files.
export const DEV = process.env.E2E_DEV === '1'
export const DEV_PORT = 3300 + SLOT
export const DEV_URL = `http://localhost:${DEV_PORT}`
export const API_URL = `http://127.0.0.1:${API_PORT}`
// "Continue with Google" against a fake Google (scripts/fake-google.mjs). Muted by default: only with
// E2E_GOOGLE=1, which also starts the fake and switches Google sign-in on in the API.
export const GOOGLE = process.env.E2E_GOOGLE === '1'
export const FAKE_GOOGLE_PORT = 8300 + SLOT
export const FAKE_GOOGLE_URL = `http://127.0.0.1:${FAKE_GOOGLE_PORT}`

// "Brain first" tests: Superaide AI credits against a fake Anthropic (scripts/fake-anthropic.mjs). Muted by
// default: only with E2E_BRAIN=1, which starts the fake and runs the API hosted, with a real model switched on.
export const BRAIN = process.env.E2E_BRAIN === '1'
export const FAKE_ANTHROPIC_PORT = 8400 + SLOT
export const FAKE_ANTHROPIC_URL = `http://127.0.0.1:${FAKE_ANTHROPIC_PORT}`

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
// The voice call v2 (ADR 0012) with fake speech services; E2E_VOICE=1 for tests/voice-intro.spec.ts.
export const VOICE = process.env.E2E_VOICE === '1'

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
    ...(BRAIN
      ? {
          FAKE_LLM: 'false',
          DEPLOY_MODE: 'hosted',
          PLATFORM_AI_ENABLED: 'true',
          PLATFORM_ANTHROPIC_API_KEY: 'sk-ant-e2e-platform-key-00000000000000',
          PLATFORM_DAILY_CAP_USD: '5',
          ANTHROPIC_BASE_URL: FAKE_ANTHROPIC_URL,
        }
      : {}),
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
    ...(VOICE ? { VOICE_INTRO_V2: 'true', VOICE_ASR: 'fake', VOICE_TTS: 'fake' } : {}),
  }
}
