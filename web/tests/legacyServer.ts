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

export type LegacyServer = { origin: string; dataDir: string; pid: number | undefined; seeded: any; persisted: () => any; stop: () => Promise<void> };
type Spawn = (command: string, args: string[], options: { env: NodeJS.ProcessEnv; stdio: ["ignore", "ignore", "pipe"] }) => ChildProcess;
// Tests of the helper's own failure paths replace the server command, shorten the waits, or
// (for kill errors no real process produces on demand) inject a fake child.
type Options = { command?: (dataDir: string, port: number) => [string, string[]]; readyTimeoutMs?: number; fetchTimeoutMs?: number; stopTimeoutMs?: number; spawn?: Spawn };

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const asError = (value: unknown) => (value instanceof Error ? value : new Error(String(value)));

export async function startLegacyServer(reserved: string[], options: Options = {}): Promise<LegacyServer> {
  const seed = spawnSync(python, ["-c", SEED], { input: JSON.stringify(reserved), env: childEnv(), encoding: "utf8" });
  if (seed.status !== 0) throw new Error("Could not build the legacy config:\n" + (seed.stderr ?? seed.error));
  const dataDir = mkdtempSync(join(tmpdir(), "bh-legacy-"));
  const stopTimeoutMs = options.stopTimeoutMs ?? 3000;
  let child: ChildProcess | undefined;
  let stderr = "";
  let exited = false;
  let markExited = () => {};
  const exit = new Promise<void>((resolve) => { markExited = resolve; });
  // Wait for the child's exit, not its stdio: a process it started may keep stderr open.
  const awaitExit = (ms: number) => (exited ? Promise.resolve() : Promise.race([exit, sleep(ms)]));
  let shuttingDown = false;
  const attempts: string[] = [];
  const signal = (name: NodeJS.Signals) => {
    try { attempts.push(`${name} returned ${child?.kill(name)}`); } catch (error) { attempts.push(`${name} threw ${asError(error).message}`); }
  };
  let stopping: Promise<void> | undefined;
  // One shutdown however often it is called. It confirms the child exited or rejects, and it
  // always tries to remove this call's own temp dir, whatever else failed.
  const stop = () => (stopping ??= (async () => {
    shuttingDown = true;
    const failures: Error[] = [];
    if (child?.pid !== undefined && !exited) {
      signal("SIGTERM");
      await awaitExit(stopTimeoutMs);
      if (!exited) {
        signal("SIGKILL");
        await awaitExit(stopTimeoutMs);
      }
      if (!exited) failures.push(new Error(`Legacy server termination unconfirmed for pid ${child.pid} after SIGTERM and SIGKILL (${attempts.join("; ")}). Its stderr so far:\n${stderr}`));
    }
    try { child?.stderr?.destroy(); } catch (error) { failures.push(asError(error)); }
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (error) { failures.push(asError(error)); }
    if (failures.length === 1) throw failures[0];
    if (failures.length) throw new AggregateError(failures, failures.map((failure) => failure.message).join("\n"));
  })());
  try {
    const settingsPath = join(dataDir, "operator-settings.json");
    writeFileSync(settingsPath, seed.stdout);
    const port = await freePort();
    const [command, args] = options.command?.(dataDir, port) ?? [python, ["-m", "balatro_horizons.cli", "review", "--data-dir", dataDir, "--host", "127.0.0.1", "--port", String(port)]];
    const spawned = (options.spawn ?? (spawn as Spawn))(command, args, { env: childEnv(), stdio: ["ignore", "ignore", "pipe"] });
    child = spawned;
    let spawnError: Error | undefined;
    spawned.stderr?.on("data", (chunk) => { stderr += chunk; });
    spawned.once("exit", () => { exited = true; markExited(); });
    // Kept for the child's whole life: an error before shutdown fails startup; during shutdown
    // (for example from kill) it is recorded with the signal attempts instead.
    spawned.on("error", (error) => {
      if (shuttingDown) attempts.push(`error ${error.message}`);
      else spawnError ??= error;
    });
    const origin = `http://127.0.0.1:${port}`;
    const readyTimeoutMs = options.readyTimeoutMs ?? 30000;
    const deadline = Date.now() + readyTimeoutMs;
    while (true) {
      if (spawnError) throw new Error(`The legacy server could not be started: ${spawnError.message}`);
      if (exited) {
        // Let what it already wrote arrive, without waiting on a pipe something else holds.
        await sleep(200);
        throw new Error("The legacy server exited:\n" + stderr);
      }
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new Error(`The legacy server did not start within ${readyTimeoutMs}ms:\n` + stderr);
      try {
        // Bounded, so a request that hangs cannot outlast the deadline.
        const abort = AbortSignal.timeout(Math.min(options.fetchTimeoutMs ?? 2000, remaining));
        if ((await fetch(origin + "/api/bootstrap", { signal: abort })).ok) break;
      } catch { /* not listening or not answering yet */ }
      await sleep(200);
    }
    return { origin, dataDir, pid: spawned.pid, seeded: JSON.parse(seed.stdout), persisted: () => JSON.parse(readFileSync(settingsPath, "utf8")), stop };
  } catch (error) {
    // The startup failure stays primary; any cleanup failure is attached, never substituted.
    const primary = asError(error);
    let cleanup: Error | undefined;
    try { await stop(); } catch (failure) { cleanup = asError(failure); }
    const failure = cleanup ? new Error(`${primary.message}\nCleanup also failed: ${cleanup.message}`, { cause: new AggregateError([primary, cleanup]) }) : primary;
    throw Object.assign(failure, { pid: child?.pid, dataDir });
  }
}
