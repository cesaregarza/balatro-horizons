import type { TimelinePoint } from "../api/client";
import { Trajectory } from "./Trajectory";

export function Comparison({ data }: { data: { interpretation: string; runs: { episode_id: string; summary: Record<string, unknown> | null; trajectory: TimelinePoint[] }[] } }) {
  return <><h1>Alternative continuation</h1><p>{data.interpretation}</p><p className="notice">Outcome exposure is recorded for both runs.</p>{data.runs.map((run, index) => <section key={run.episode_id}><h2>{index ? "Branch" : "Original run"}</h2><pre>{JSON.stringify(run.summary, null, 2)}</pre><Trajectory points={run.trajectory} /></section>)}</>;
}
