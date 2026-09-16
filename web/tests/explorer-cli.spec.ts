import { test, expect } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

test("browser explorer verification help and invalid arguments need no browser", () => {
  const script = fileURLToPath(new URL("../../scripts/verify_browser_native.mjs", import.meta.url));
  const help = spawnSync(process.execPath, [script, "--help"], { encoding: "utf8" });
  expect(help.status).toBe(0);
  expect(help.stdout).toContain("--explore EPISODE_ID [DECISION_ID]");
  for (const args of [["bad", "0"], ["e".repeat(32), "-1"], ["e".repeat(32), "1.5"], ["e".repeat(32), ""]]) {
    const result = spawnSync(process.execPath, [script, "--explore", ...args], { encoding: "utf8" });
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Usage:");
  }
});
