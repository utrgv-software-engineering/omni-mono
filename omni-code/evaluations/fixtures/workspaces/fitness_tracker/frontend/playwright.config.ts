import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 0,
  workers: 1,
  use: {
    baseURL: 'http://localhost:5173',
    headless: true,
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
  webServer: [
    {
      command: 'rm -f fitness_tracker_test.db && uv run uvicorn fitness_tracker.main:app --port 8000',
      cwd: '..',
      port: 8000,
      reuseExistingServer: false,
      env: {
        ...process.env,
        DATABASE_URL: 'sqlite:///./fitness_tracker_test.db',
      },
    },
    {
      command: 'npm run dev',
      port: 5173,
      reuseExistingServer: false,
    },
  ],
});
