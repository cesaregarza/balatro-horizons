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
// Tests of the helper's own failure paths replace the server command and shorten the waits.
type Options = { command?: (dataDir: string, port: number) => [string, string[]]; readyTimeoutMs?: number; fetchTimeoutMs?: number };

export async function startLegacyServer(reserved: string[], options: Options = {}): Promise<LegacyServer> {
  const seed = spawnSync(python, ["-c", SEED], { input: JSON.stringify(reserved), env: childEnv(), encoding: "utf8" });
  if (seed.status !== 0) throw new Error("Could not build the legacy config:\n" + (seed.stderr ?? seed.error));
  const dataDir = mkdtempSync(join(tmpdir(), "bh-legacy-"));
  let child: ChildProcess | undefined;
  let exited: Promise<void> = Promise.resolve();
  // Every failure from here on stops the child and removes this helper's own temp dir.
  const stop = async () => {
    if (child && child.pid !== undefined && child.exitCode === null && child.signalCode === null) {
      child.kill("SIGTERM");
      const killed = setTimeout(() => child?.kill("SIGKILL"), 3000);
      await exited;
      clearTimeout(killed);
    }
    rmSync(dataDir, { recursive: true, force: true });
  };
  try {
    const settingsPath = join(dataDir, "operator-settings.json");
    writeFileSync(settingsPath, seed.stdout);
    const port = await freePort();
    const [command, args] = options.command?.(dataDir, port) ?? [python, ["-m", "balatro_horizons.cli", "review", "--data-dir", dataDir, "--host", "127.0.0.1", "--port", String(port)]];
    const spawned = spawn(command, args, { env: childEnv(), stdio: ["ignore", "ignore", "pipe"] });
    child = spawned;
    let stderr = "";
    let spawnError: Error | undefined;
    spawned.stderr?.on("data", (chunk) => { stderr += chunk; });
    exited = new Promise<void>((resolve) => spawned.once("close", () => resolve()));
    spawned.once("error", (error) => { spawnError = error; });
    const origin = `http://127.0.0.1:${port}`;
    const deadline = Date.now() + (options.readyTimeoutMs ?? 30000);
    while (true) {
      if (spawnError) throw new Error(`The legacy server could not be started: ${spawnError.message}`);
      if (spawned.exitCode !== null || spawned.signalCode !== null) throw new Error("The legacy server exited:\n" + stderr);
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new Error(`The legacy server did not start within ${options.readyTimeoutMs ?? 30000}ms:\n` + stderr);
      try {
        // Bounded, so a request that hangs cannot outlast the deadline.
        const signal = AbortSignal.timeout(Math.min(options.fetchTimeoutMs ?? 2000, remaining));
        if ((await fetch(origin + "/api/bootstrap", { signal })).ok) break;
      } catch { /* not listening or not answering yet */ }
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    return { origin, seeded: JSON.parse(seed.stdout), persisted: () => JSON.parse(readFileSync(settingsPath, "utf8")), stop };
  } catch (error) {
    await stop();
    throw error;
  }
}
