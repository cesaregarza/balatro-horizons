import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// A real dashboard server booted from a legacy operator config that the settings route would
// now refuse (model aliases named like built-in players). It runs like playwright.config's
// webServer, but on its own data dir and port, so tests that repair it cannot leak.
const root = fileURLToPath(new URL("../../", import.meta.url));
const python = `${root}.venv/bin/python`;

function childEnv() {
  const { OPENAI_API_KEY: _openai, ANTHROPIC_API_KEY: _anthropic, ...env } = process.env;
  return { ...env, PYTHONPATH: `${root}src` };
}

// Built by the real Config so the seed is exactly what an older install could have saved.
const SEED = `
import json, sys
from balatro_horizons.config import ROOT, load_config
config = load_config()
luna = load_config(ROOT / "configs/luna-smoke.yaml").models["luna"]
reserved = json.load(sys.stdin)
config.models = {key: luna.model_copy(update={"input_usd_per_million": float(index)})
                 for index, key in enumerate(reserved, start=1)}
config.models["luna2"] = luna.model_copy(update={"input_usd_per_million": 9.0})
print(json.dumps(config.model_dump()))
`;

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => (address && typeof address === "object" ? resolve(address.port) : reject(new Error("no port"))));
    });
  });
}

export type LegacyServer = { origin: string; seeded: any; persisted: () => any; stop: () => Promise<void> };

export async function startLegacyServer(reserved: string[]): Promise<LegacyServer> {
  const seed = spawnSync(python, ["-c", SEED], { input: JSON.stringify(reserved), env: childEnv(), encoding: "utf8" });
  if (seed.status !== 0) throw new Error("Could not build the legacy config:\n" + seed.stderr);
  const dataDir = mkdtempSync(join(tmpdir(), "bh-legacy-"));
  const settingsPath = join(dataDir, "operator-settings.json");
  writeFileSync(settingsPath, seed.stdout);
  const port = await freePort();
  const child: ChildProcess = spawn(python, ["-m", "balatro_horizons.cli", "review", "--data-dir", dataDir, "--host", "127.0.0.1", "--port", String(port)], { env: childEnv(), stdio: ["ignore", "ignore", "pipe"] });
  let stderr = "";
  child.stderr?.on("data", (chunk) => { stderr += chunk; });
  const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
  const stop = async () => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGTERM");
      const killed = setTimeout(() => child.kill("SIGKILL"), 3000);
      await exited;
      clearTimeout(killed);
    }
    rmSync(dataDir, { recursive: true, force: true });
  };
  const origin = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 30000;
  while (true) {
    if (child.exitCode !== null) { await stop(); throw new Error("The legacy server exited:\n" + stderr); }
    try {
      if ((await fetch(origin + "/api/bootstrap")).ok) break;
    } catch { /* not listening yet */ }
    if (Date.now() > deadline) { await stop(); throw new Error("The legacy server did not start within 30s:\n" + stderr); }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return { origin, seeded: JSON.parse(seed.stdout), persisted: () => JSON.parse(readFileSync(settingsPath, "utf8")), stop };
}
