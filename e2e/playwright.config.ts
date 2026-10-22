import { defineConfig, devices } from '@playwright/test'
import { API_PORT, API_URL, BASE_URL, DB_NAME, FAKE_GOOGLE_PORT, FAKE_GOOGLE_URL, GOOGLE, PG_ADMIN_URL, SLOT, WEB_PORT, apiEnv } from './src/env'

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
    { name: 'desktop', use: { ...devices['Desktop Chrome'] }, testIgnore: /\.mobile\.spec\.ts$/ },
    { name: 'mobile', use: { ...devices['Pixel 7'] }, testMatch: /\.mobile\.spec\.ts$/ },
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
  ],
})
