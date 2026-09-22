import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

test("browser explorer verification help and invalid arguments need no browser", () => {
  const script = fileURLToPath(
    new URL("../scripts/verify_browser_native.mjs", import.meta.url),
  );
  const help = spawnSync(process.execPath, [script, "--help"], {
    encoding: "utf8",
  });
  assert.equal(help.status, 0);
  assert.equal(help.stdout.includes("--explore EPISODE_ID [DECISION_ID]"), true);
  for (const args of [
    ["bad", "0"],
    ["e".repeat(32), "-1"],
    ["e".repeat(32), "1.5"],
    ["e".repeat(32), ""],
  ]) {
    const result = spawnSync(process.execPath, [script, "--explore", ...args], {
      encoding: "utf8",
    });
    assert.equal(result.status, 1);
    assert.equal(result.stderr.includes("Usage:"), true);
  }
});
