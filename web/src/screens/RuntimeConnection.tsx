import { useEffect, useState } from "react";
import { operatorRuntime, type RuntimeConnection as RuntimeConnectionState } from "../api/client";

const unreachable: RuntimeConnectionState = {
  ready: false,
  code: "UNREACHABLE",
  message: "Cannot check the runtime connection. Check that the backend is available.",
};

export function useRuntimeConnection(enabled: boolean) {
  const [connection, setConnection] = useState<RuntimeConnectionState | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setConnection(null);
      return;
    }
    const controller = new AbortController();
    let active = true;
    setConnection(null);
    operatorRuntime(controller.signal)
      .then((status) => { if (active) setConnection(status); })
      .catch(() => {
        if (active && !controller.signal.aborted) setConnection(unreachable);
      });
    return () => { active = false; controller.abort(); };
  }, [enabled, refreshKey]);

  return { connection, refresh: () => setRefreshKey((value) => value + 1) };
}

export function RuntimeConnection({ connection, onRefresh }: {
  connection: RuntimeConnectionState | null;
  onRefresh: () => void;
}) {
  if (!connection) return <p role="status" className="muted">Checking the runtime connection…</p>;
  return <div className="runtime-connection">
    <p role="status" className={connection.ready ? "muted" : "error"}>{connection.message}</p>
    <button type="button" onClick={onRefresh}>Refresh connection</button>
  </div>;
}
