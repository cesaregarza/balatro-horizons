import { useEffect, useState } from "react";
import { decisionDetail, type View } from "../../api/client";

export function ExactRecords({ token, decision }: { token: string; decision: number }) {
  const [open, setOpen] = useState(false);
  const [records, setRecords] = useState<View["action_events"]>();
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open || records) return;
    const controller = new AbortController();
    setError("");
    decisionDetail(token, decision, true, controller.signal).then((view) => {
      if (!controller.signal.aborted) setRecords(view.action_events || []);
    }).catch(() => { if (!controller.signal.aborted) setError("Could not load the technical records."); });
    return () => controller.abort();
  }, [token, decision, open, records, retry]);
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Exact public decision records</summary>
    {error ? <p role="alert">{error} <button onClick={() => setRetry((value) => value + 1)}>Retry records</button></p> : records ? <pre>{JSON.stringify(records, null, 2)}</pre> : <p role="status">Loading technical records…</p>}
  </details>;
}
