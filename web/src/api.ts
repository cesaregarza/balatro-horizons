export type Card = {
  id: string;
  label: string;
  face_down: boolean;
  rank: string | null;
  suit: string | null;
  effects: string[];
  counters: Record<string, string>;
  sellable: boolean | null;
  usable: boolean;
  min_targets: number;
  max_targets: number;
};
export type Offer = {
  id: string;
  label: string;
  kind: string;
  price: string | null;
  effects: string[];
  acquire_allowed: boolean;
  buy_and_use_allowed: boolean;
  min_targets: number;
  max_targets: number;
  face_down?: boolean;
  rank?: string | null;
  suit?: string | null;
};
export type PublicEffect = { label: string; effects: string[] };
export type Observation = {
  schema_version?: "1.0" | "1.1";
  episode_id: string;
  observation_id: number;
  phase: string;
  memory: string;
  available_action_types: string[];
  action_constraints: { reorder?: { areas: string[] } };
  remaining_budget: Record<string, number>;
  state: {
    progress: { ante: number | null; blind: string | null };
    resources: Record<string, string | number | null>;
    hand: Card[];
    jokers: Card[];
    consumables: Card[];
    offers: Offer[];
    revealed_blinds: {
      id: string;
      label: string;
      target: string;
      skip_allowed: boolean;
      effects: string[];
      status?:
        | "SELECT"
        | "CURRENT"
        | "UPCOMING"
        | "DEFEATED"
        | "SKIPPED"
        | "UNKNOWN";
      disabled?: boolean | null;
      skip_reward?:
        | (PublicEffect & { acquisition_condition: "skip_this_blind" })
        | null;
    }[];
    hand_levels: Record<string, string>;
    persistent_effects: string[];
    owned_vouchers?: PublicEffect[] | null;
    pending_tags?: PublicEffect[] | null;
  };
};
export type Action = Record<string, unknown>;
export type DecisionRow = {
  event_id: string;
  decision: number;
  action_number?: number;
  ante: number | null;
  blind?: string | null;
  phase: string;
  type: string;
  note: string | null;
  item?: string;
  effects?: string[];
  mode?: string;
  cards?: string[];
  targets?: string[];
  area?: string;
  ordered_objects?: string[];
  ordering_before?: string[];
  hand_types?: string[];
  score?: string;
  total_chips?: string;
  target_before?: string;
  target_after?: string;
  hands_after?: number;
  discards_after?: number;
  money_before?: string;
  money_after?: string;
  money_change?: string;
  jokers_after?: string[];
  jokers_added?: string[];
  jokers_removed?: string[];
  status?: string;
  rejection_code?: string;
};
export type DecisionLedger = {
  source_journal_head: string | null;
  manifest: {
    episode_id: string;
    agent: string;
    evidence_kind: string;
    evaluation_eligible: boolean;
    fixture?: string | null;
    recorded_interface?: string | null;
    current_harness?: boolean;
    config?: {
      deck?: string;
      stake?: string;
      models?: Record<string, import("./modelSelection").ModelConfig>;
    };
  };
  summary: { outcome?: string; reason?: string; cost_usd?: number } | null;
  actions: DecisionRow[];
  uncommitted_actions: DecisionRow[] | null;
  rounds:
    | {
        ante: number;
        blind: string;
        target: string;
        total_chips?: string;
        cleared: boolean;
      }[]
    | null;
};
export type View = {
  episode_id: string;
  decision: number;
  stage: "observation" | "action" | "transition";
  observation: Observation;
  evidence_kind: string;
  evaluation_eligible: boolean;
  fixture: string | null;
  action_events?: { type: string; payload: unknown }[];
  transition?: Observation;
  terminal?: Record<string, unknown>;
  can_advance?: boolean;
  review_mode: string;
  exposure: Record<string, unknown>;
  trajectory: import("./Trajectory").TimelinePoint[];
};
let operatorToken = "";
export function setOperatorToken(token: string) {
  operatorToken = token;
}
export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
  reviewToken?: string,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (reviewToken) headers["X-Review-Token"] = reviewToken;
  else headers["X-BH-Operator"] = operatorToken;
  const response = await fetch("/api" + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      String(data.error || data.detail || "Request failed").startsWith(
        "WINDOWS_SESSION_",
      )
        ? "Windows runtime connection needs refreshing. Run scripts/configure_workbench_session.py --apply from a Windows-connected WSL terminal. No episode was started."
        : data.error || data.detail || "Request failed",
    );
  return data;
}

export async function download(path: string, filename: string) {
  const response = await fetch("/api" + path, {
    headers: { "X-BH-Operator": operatorToken },
  });
  if (!response.ok) throw new Error("Public export download failed");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
