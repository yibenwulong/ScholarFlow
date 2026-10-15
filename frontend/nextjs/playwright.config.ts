import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

const port = process.env.PLAYWRIGHT_PORT || '3100';
const live = process.env.SCHOLARFLOW_LIVE_TEST === 'true';
const apiPort = process.env.PLAYWRIGHT_API_PORT || '8100';
const python = process.env.PLAYWRIGHT_PYTHON || path.resolve(__dirname, '../..', process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const frontend = { command: `npm run dev -- --hostname 127.0.0.1 --port ${port}`,
  url: `http://127.0.0.1:${port}/img/gptr-black-logo.png`, reuseExistingServer: false, timeout: 120000,
  env: { NEXT_TELEMETRY_DISABLED: '1', SCHOLARFLOW_API_URL: `http://127.0.0.1:${apiPort}` } };
export default defineConfig({
  testDir: './e2e', timeout: 45000, expect: { timeout: 10000 }, fullyParallel: false,
  use: { baseURL: `http://127.0.0.1:${port}`, trace: 'retain-on-failure',
    ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { launchOptions: { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } }
      : process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) },
  projects: [{ name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } }],
  webServer: live ? [frontend, { command: `"${python}" scripts/scholarflow_demo.py --port ${apiPort} --data-dir .tmp-tests/live-${port}-${Date.now()}`,
    cwd: path.resolve(__dirname, '../..'), url: `http://127.0.0.1:${apiPort}/api/v1/status`, timeout: 120000, reuseExistingServer: false }] : frontend,
});
