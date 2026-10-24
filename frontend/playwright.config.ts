import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:8001",
    viewport: { width: 1280, height: 900 },
    trace: "off",
    video: "off",
  },
  webServer: {
    command:
      "cd .. && SAMPLE_MODE=true .venv/bin/uvicorn redrun.api.app:app --host 127.0.0.1 --port 8001",
    url: "http://127.0.0.1:8001/health",
    reuseExistingServer: true,
    timeout: 60_000,
    stdout: "ignore",
    stderr: "pipe",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
