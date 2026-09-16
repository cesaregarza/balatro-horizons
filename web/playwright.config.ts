import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../", import.meta.url));
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  use: {
    baseURL: "http://127.0.0.1:8766",
    headless: true,
    viewport: { width: 1440, height: 1100 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: `${root}.venv/bin/python -m balatro_horizons.cli review --data-dir ${root}web/.e2e-data --port 8766`,
    env: { PYTHONPATH: `${root}src` },
    url: "http://127.0.0.1:8766",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
