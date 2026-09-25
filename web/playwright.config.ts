import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../", import.meta.url));
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  use: {
    headless: true,
    viewport: { width: 1440, height: 1100 },
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "dashboard",
      testMatch: /(?:card-modifiers|cost-overrides|decisions|dev-trace|model-selection|polling|public-information|run-spend|runtime-connection)\.spec\.ts/,
      use: { baseURL: "http://127.0.0.1:8766" },
    },
    {
      name: "workbench",
      testMatch: /(?:budget-continuation|workbench|restore)\.spec\.ts/,
      use: { baseURL: "http://127.0.0.1:8767" },
    },
  ],
  webServer: [
    {
      command: `${root}.venv/bin/python -m balatro_horizons.cli review --data-dir ${root}web/.e2e-data-dashboard --port 8766`,
      env: { PYTHONPATH: `${root}src` },
      url: "http://127.0.0.1:8766",
      reuseExistingServer: false,
      timeout: 30000,
    },
    {
      command: `${root}.venv/bin/python -m balatro_horizons.cli review --workbench --data-dir ${root}web/.e2e-data-workbench --port 8767`,
      env: { PYTHONPATH: `${root}src` },
      url: "http://127.0.0.1:8767",
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});
