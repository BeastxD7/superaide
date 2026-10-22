import { defineConfig, devices } from '@playwright/test'
import { BRAIN, FAKE_ANTHROPIC_PORT, FAKE_ANTHROPIC_URL, API_PORT, API_URL, DEV, DEV_PORT, DEV_URL, PROXY, PROXY_A, PROXY_B, PROXY_C, UPSTREAM_PORT, BASE_URL, DB_NAME, FAKE_GOOGLE_PORT, FAKE_GOOGLE_URL, GOOGLE, PG_ADMIN_URL, SLOT, WEB_PORT, apiEnv } from './src/env'

// One slot = one API, one Next.js server and one database (see src/env.ts).
// Tests inside a slot run one at a time because they reset the shared database;
// run several slots (E2E_SLOT=0,1,2…) or CI shards for parallelism.
export default defineConfig({
  testDir: './tests',
  outputDir: `./test-results/slot-${SLOT}`,
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI
    ? [['github'], ['html', { open: 'never', outputFolder: `playwright-report/slot-${SLOT}` }]]
    : [['list'], ['html', { open: 'never', outputFolder: `playwright-report/slot-${SLOT}` }]],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    // The introduction's video call: grant the camera and microphone with fake devices.
    // --mute-audio: tests never play sound (the fake devices beep; see also silence() in src/fixtures.ts).
    permissions: ['camera', 'microphone'],
    launchOptions: { args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--mute-audio'] },
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] }, testIgnore: /\.(mobile|dev|proxy)\.spec\.ts$/ },
    { name: 'mobile', use: { ...devices['Pixel 7'] }, testMatch: /\.mobile\.spec\.ts$/ },
    // The /api proxy against a fake upstream (E2E_PROXY=1): no browser, no database.
    ...(PROXY ? [{ name: 'proxy', testMatch: /\.proxy\.spec\.ts$/ }] : []),
    // Against `next dev` (E2E_DEV=1): React runs every effect twice there, which the production build never does.
    ...(DEV ? [{ name: 'dev', use: { ...devices['Desktop Chrome'], baseURL: DEV_URL }, testMatch: /\.dev\.spec\.ts$/ }] : []),
  ],
  webServer: [
    // A fake Google for the "Continue with Google" tests (E2E_GOOGLE=1 only).
    ...(GOOGLE
      ? [
          {
            command: 'node scripts/fake-google.mjs',
            url: `${FAKE_GOOGLE_URL}/health`,
            env: { ...process.env, FAKE_GOOGLE_PORT: String(FAKE_GOOGLE_PORT) },
            reuseExistingServer: !process.env.CI,
            timeout: 30_000,
            stdout: 'ignore' as const,
            stderr: 'pipe' as const,
          },
        ]
      : []),
    // A fake Anthropic for the "brain first" tests (E2E_BRAIN=1 only).
    ...(BRAIN
      ? [
          {
            command: 'node scripts/fake-anthropic.mjs',
            url: `${FAKE_ANTHROPIC_URL}/health`,
            env: { ...process.env, FAKE_ANTHROPIC_PORT: String(FAKE_ANTHROPIC_PORT) },
            reuseExistingServer: !process.env.CI,
            timeout: 30_000,
            stdout: 'ignore' as const,
            stderr: 'pipe' as const,
          },
        ]
      : []),
    {
      command: 'bash scripts/start-api.sh',
      url: `${API_URL}/health`,
      env: { ...process.env, ...apiEnv(), E2E_PG_ADMIN_URL: PG_ADMIN_URL, E2E_DB_NAME: DB_NAME, E2E_API_PORT: String(API_PORT) },
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      stdout: 'ignore',
      stderr: 'pipe',
    },
    {
      // Needs a production build first: `npm run build` in frontend/ (make e2e does it).
      command: `npx next start --port ${WEB_PORT}`,
      cwd: '../frontend',
      url: `http://127.0.0.1:${WEB_PORT}/api/health`,
      env: { ...process.env, API_INTERNAL_URL: API_URL, NEXT_TELEMETRY_DISABLED: '1' },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: 'ignore',
      stderr: 'pipe',
    },
    // The proxy contract tests (E2E_PROXY=1): a fake upstream and three Next servers in front of it.
    ...(PROXY
      ? [
          {
            command: 'node scripts/fake-upstream.mjs',
            url: `http://127.0.0.1:${UPSTREAM_PORT}/health`,
            env: { ...process.env, FAKE_UPSTREAM_PORT: String(UPSTREAM_PORT) },
            reuseExistingServer: !process.env.CI,
            timeout: 30_000,
            stdout: 'ignore' as const,
            stderr: 'pipe' as const,
          },
          ...[
            [PROXY_A, `http://127.0.0.1:${UPSTREAM_PORT}`, 'false'],
            [PROXY_B, `http://127.0.0.1:${UPSTREAM_PORT}`, 'true'],
            [PROXY_C, 'http://127.0.0.1:1', 'false'],
          ].map(([port, upstream, trust]) => ({
            command: `npx next start --port ${port}`,
            cwd: '../frontend',
            // The Next server itself answers /icon.svg without the upstream, so the one that cannot reach it still starts.
            url: `http://127.0.0.1:${port}/icon.svg`,
            env: { ...process.env, API_INTERNAL_URL: String(upstream), TRUST_PROXY_HEADERS: String(trust), NEXT_TELEMETRY_DISABLED: '1' },
            reuseExistingServer: !process.env.CI,
            timeout: 120_000,
            stdout: 'ignore' as const,
            stderr: 'pipe' as const,
          })),
        ]
      : []),
    ...(DEV
      ? [
          {
            command: `npx next dev --port ${DEV_PORT}`,
            cwd: '../frontend',
            url: `http://127.0.0.1:${DEV_PORT}/api/health`,
            env: { ...process.env, API_INTERNAL_URL: API_URL, NEXT_TELEMETRY_DISABLED: '1', NEXT_DIST_DIR: `.next-dev-${SLOT}` },
            reuseExistingServer: !process.env.CI,
            timeout: 180_000,
            stdout: 'ignore' as const,
            stderr: 'pipe' as const,
          },
        ]
      : []),
  ],
})
