import { useState } from "react";
import type { Observation } from "./api";
export type TimelinePoint = {
  decision: number;
  phase: string;
  resources: Observation["state"]["resources"];
  progress: Observation["state"]["progress"];
  build: string[];
};
export function Trajectory({ points }: { points: TimelinePoint[] }) {
  const [metric, setMetric] = useState("money");
  const [selected, setSelected] = useState<number | null>(null);
  const values = points.map((p) =>
    p.resources[metric] == null ? null : Number(p.resources[metric]),
  );
  const finite = values.filter(
    (v): v is number => v !== null && Number.isFinite(v),
  );
  const low = Math.min(0, ...finite),
    high = Math.max(low + 1, ...finite);
  const x = (i: number) => 48 + (704 * i) / Math.max(points.length - 1, 1);
  const y = (v: number) => 145 - (120 * (v - low)) / (high - low);
  const current = points[selected ?? points.length - 1];
  return (
    <section className="panel">
      <div className="form-row">
        <h3>Revealed trajectory</h3>
        <label>
          Resource
          <select value={metric} onChange={(e) => setMetric(e.target.value)}>
            {[
              "money",
              "chips",
              "target",
              "hands",
              "discards",
              "joker_capacity",
            ].map((k) => (
              <option key={k} value={k}>
                {k.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
      </div>
      <svg
        viewBox="0 0 800 175"
        role="img"
        aria-label={`${metric} over revealed decisions`}
      >
        <text x="0" y="28" fill="currentColor" fontSize="12">
          {high.toLocaleString()}
        </text>
        <text x="0" y="148" fill="currentColor" fontSize="12">
          {low.toLocaleString()}
        </text>
        <line x1="48" y1="145" x2="752" y2="145" stroke="#617286" />
        {values.map((v, i) =>
          v !== null && Number.isFinite(v) ? (
            <g key={i}>
              {i > 0 &&
                values[i - 1] !== null &&
                Number.isFinite(values[i - 1]) && (
                  <line
                    x1={x(i - 1)}
                    y1={y(values[i - 1]!)}
                    x2={x(i)}
                    y2={y(v)}
                    stroke="#ebc773"
                    strokeWidth="2"
                  />
                )}
              <circle cx={x(i)} cy={y(v)} r="5" fill="#ebc773">
                <title>
                  Decision {points[i].decision}: {v}
                </title>
              </circle>
            </g>
          ) : null,
        )}
        <text x="48" y="169" fill="currentColor" fontSize="12">
          Decision {points[0]?.decision}
        </text>
        <text x="690" y="169" fill="currentColor" fontSize="12">
          {points.at(-1)?.decision}
        </text>
      </svg>
      <label>
        Inspect revealed decision
        <select
          value={selected ?? points.length - 1}
          onChange={(e) => setSelected(+e.target.value)}
        >
          {points.map((p, i) => (
            <option key={p.decision} value={i}>
              {p.decision} · {p.phase.toLowerCase().replaceAll("_", " ")}
            </option>
          ))}
        </select>
      </label>
      {current && (
        <p className="muted">
          Ante {current.progress.ante} · {current.progress.blind} ·{" "}
          {current.build.length ? current.build.join(" · ") : "No Jokers"}
        </p>
      )}
    </section>
  );
}
