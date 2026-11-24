import { useState } from "react";
import { cloudStatus, type CloudAlert, type CloudRun, type CloudStatus } from "../api/client";
import { usePolling } from "../usePolling";
import "./cloud-runs.css";

const REFRESH_MS = 5000;
const READ_FAILURES: Record<string, string> = {
  CLOUD_LEDGER_UNREADABLE: "A cloud record on the server could not be read, so no run list is shown.",
  CLOUD_SETTINGS_INVALID: "The cloud settings file is missing or invalid, so no status is shown.",
};
// Absolute UTC, so every device reads a record at the same time the server wrote it.
const utcTime = new Intl.DateTimeFormat("en-GB", {
  year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  hourCycle: "h23", timeZone: "UTC", timeZoneName: "short",
});
const when = (value: string) => { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : utcTime.format(date); };
const usd = (value: number) => `$${value.toFixed(4)}`;

// The browser shows what the server reports and works out nothing itself.
export function CloudRuns() {
  const [status, setStatus] = useState<CloudStatus | null>(null);
  const [failure, setFailure] = useState("");
  usePolling(true, REFRESH_MS, cloudStatus,
    (next) => { setStatus(next); setFailure(""); },
    // A failed read replaces the report: an old list would read as the current one.
    (caught) => { setStatus(null); setFailure(caught instanceof Error ? caught.message : String(caught)); });
  return <>
    <h1>Cloud runs</h1>
    {failure && <div role="alert" className="error"><p>{READ_FAILURES[failure] ?? "The cloud status could not be read."}</p><code>{failure}</code></div>}
    {!status && !failure && <p role="status">Reading the cloud records…</p>}
    {status && <Report status={status} />}
  </>;
}

function Report({ status }: { status: CloudStatus }) {
  return <>
    {status.alerts.length > 0 && <section className="cloud-alerts" aria-label="Active alerts">{status.alerts.map((alert) => <Alert key={`${alert.code}-${alert.run_id}-${alert.droplet_id}`} alert={alert} />)}</section>}
    <p className="muted">Read by the server at {when(status.read_at)}. Refreshed every {REFRESH_MS / 1000} seconds.</p>
    <section className="panel" aria-label="Launches">
      <h2>{status.launches === "ALLOWED" ? "Launches are allowed" : "Launches are refused"}</h2>
      {status.refusals.length > 0 && <ul className="cloud-refusals">{status.refusals.map((code) => <li key={code}><code>{code}</code></li>)}</ul>}
    </section>
    <section className="panel" aria-label="Budget">
      <h2>Budget</h2>
      <dl className="cloud-facts"><dt>Total</dt><dd>{usd(status.budget_usd.total)}</dd><dt>Remaining</dt><dd>{usd(status.budget_usd.remaining)}</dd><dt>Worst case per run</dt><dd>{usd(status.budget_usd.worst_case_per_run)}</dd></dl>
    </section>
    <section className="panel" aria-label="Inventory">
      <h2>Inventory</h2>
      {status.inventory ? <p>Last read {when(status.inventory.checked_at)} · {status.inventory.complete ? "complete" : "incomplete"}</p> : <p>The inventory has not been read yet.</p>}
    </section>
    <RunTable runs={status.runs} />
  </>;
}

function Alert({ alert }: { alert: CloudAlert }) {
  return <article role="alert" className="error cloud-alert"><p><b>{alert.message}</b></p><p><code>{alert.code}</code> · since {when(alert.since)}</p></article>;
}

function RunTable({ runs }: { runs: CloudRun[] }) {
  return <section className="panel" aria-label="Run records">
    <h2>Runs</h2>
    {runs.length === 0 ? <p>No cloud runs are recorded yet.</p> : <div className="table-wrap"><table className="cloud-runs">
      <thead><tr><th>Run</th><th>State</th><th>Created</th><th>Original deadline</th><th>Teardown reason</th><th>Cost</th><th>Reserved</th><th>Settled</th></tr></thead>
      <tbody>{runs.map((run) => <tr key={run.run_id}>
        <td><code>{run.run_id}</code></td><td>{run.state}</td><td>{when(run.created_at)}</td><td>{when(run.deadline)}</td>
        <td>{run.teardown_reason ?? "—"}</td><td>{usd(run.cost_usd)}</td><td>{usd(run.reserved_usd)}</td><td>{run.settled_usd === null ? "—" : usd(run.settled_usd)}</td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}
