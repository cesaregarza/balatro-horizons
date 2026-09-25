import { useState } from "react";
import { title, type ObjectNames } from "../../devTracePresentation";

export function JsonPanel({ label, value }: { label: string; value: unknown }) {
  const [open, setOpen] = useState(false);
  return <details className="dev-json" onToggle={(event) => setOpen(event.currentTarget.open)}><summary>{label}</summary>{open && <pre>{JSON.stringify(value, null, 2) ?? "Not recorded"}</pre>}</details>;
}

export function ReadableValue({ value, names = new Map(), depth = 0 }: { value: unknown; names?: ObjectNames; depth?: number }) {
  const [expanded, setExpanded] = useState(false);
  const [count, setCount] = useState(8);
  if (value == null) return <span className="muted">Not recorded</span>;
  if (typeof value === "string") {
    if (/^\s*[\[{]/.test(value)) {
      try { return <ReadableValue value={JSON.parse(value)} names={names} depth={depth} />; } catch { /* show verbatim */ }
    }
    const text = names.get(value) || value;
    return <span className="dev-value-text">{expanded ? text : text.slice(0, 600)}{text.length > 600 && <button className="dev-expand" onClick={() => setExpanded(!expanded)}>{expanded ? "Show less" : `Read full text (${text.length.toLocaleString()} characters)`}</button>}</span>;
  }
  if (typeof value !== "object") return <span>{typeof value === "boolean" ? (value ? "Yes" : "No") : String(value)}</span>;
  const entries = Array.isArray(value) ? value.map((child, index) => [String(index + 1), child] as const) : Object.entries(value);
  if (!entries.length) return <span className="muted">{Array.isArray(value) ? "None" : "No fields"}</span>;
  if (depth >= 2 && !expanded) return <button className="dev-expand" onClick={() => setExpanded(true)}>Show {entries.length} {Array.isArray(value) ? "items" : "fields"}</button>;
  return <div className="dev-value"><dl>{entries.slice(0, count).map(([key, child]) => <div key={key}><dt>{title(key)}</dt><dd><ReadableValue value={child} names={names} depth={depth + 1} /></dd></div>)}</dl>{entries.length > count && <button className="dev-expand" onClick={() => setCount(count + 16)}>Show more ({entries.length - count} remaining)</button>}</div>;
}
