import { defineConfig } from "@playwright/test";

// End-to-end tests run the web app against a mocked API (see tests/e2e/fixtures.ts), so they
// need no backend or database. Set PLAYWRIGHT_CHANNEL="" to use Playwright's bundled Chromium.
// Next allows one dev server per app, so locally the tests reuse a running `npm run dev`.
const port = Number(process.env.E2E_PORT ?? 3000);

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://localhost:${port}`,
    // Locally use the installed Chrome; PLAYWRIGHT_CHANNEL="" (CI) uses the bundled Chromium.
    channel: process.env.PLAYWRIGHT_CHANNEL === undefined ? "chrome" : process.env.PLAYWRIGHT_CHANNEL || undefined,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `npx next dev --port ${port}`,
    url: `http://localhost:${port}`,
    timeout: 180_000,
    reuseExistingServer: !process.env.CI,
  },
});
