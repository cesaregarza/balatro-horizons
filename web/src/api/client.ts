import type { CapabilityTable, ModelConfig } from "../modelSelection";

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
      status?: string;
      disabled?: boolean | null;
      skip_reward?: (PublicEffect & { acquisition_condition: "skip_this_blind" }) | null;
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
export type RunSpendTotals = {
  accounted_usd: number | null;
  response_usd: number | null;
  reserved_usd: number | null;
  prior_usd?: number | null;
  combined_usd?: number | null;
};
export type RestorePlan = {
  parent_head: string;
  plan_hash: string;
  decision: number;
  requires_paid_authorization: boolean;
  source_compatibility: "same_source" | "compatible_update";
  costs: { accounted_usd: number; remaining_episode_usd: number | null; remaining_batch_usd: number | null };
  limits: { max_episode_cost_usd: number | "uncapped" | null; max_batch_cost_usd: number | "uncapped" | null };
  launches: { verification: 0; continuation: 1 };
};
export type RestorePreview = { episode_id: string; available: boolean; reason: string | null; plan: RestorePlan | null };
export type BudgetContinuationPlan = {
  parent_terminal_hash: string;
  plan_hash: string;
  decision: number;
  accounted_usd: number;
  additional_usd: 10;
  additional_available: boolean;
  additional_reason: string | null;
  new_cap_usd: number;
  source_compatibility: "same_source" | "compatible_update";
};
export type BudgetContinuationPreview = {
  episode_id: string;
  available: boolean;
  reason: string | null;
  plan: BudgetContinuationPlan | null;
};
export type BudgetContinuationInput =
  | {
      parent_terminal_hash: string;
      plan_hash: string;
      additional_cost_usd: 10;
      authorize_paid: true;
      accept_compatible_update: boolean;
    }
  | {
      parent_terminal_hash: string;
      plan_hash: string;
      combined_cap_usd: "uncapped";
      authorize_paid: true;
      confirm_uncapped: true;
      accept_compatible_update: boolean;
    };
export type DecisionLedger = {
  source_journal_head: string | null;
  manifest: {
    episode_id: string;
    parent_episode_id?: string | null;
    batch_id?: string | null;
    agent: string;
    evidence_kind: string;
    evaluation_eligible: boolean;
    fixture?: string | null;
    recorded_interface?: string | null;
    current_harness?: boolean;
    config?: {
      deck?: string;
      stake?: string;
      models?: Record<string, ModelConfig>;
      model_capabilities?: CapabilityTable;
    };
  };
  summary: { outcome?: string; reason?: string; cost_usd?: number } | null;
  spend?: RunSpendTotals;
  actions: DecisionRow[];
  uncommitted_actions: DecisionRow[] | null;
  pending_decisions?: DecisionRow[];
  rounds: { ante: number; blind: string; target: string; cleared: boolean }[] | null;
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
  trajectory: TimelinePoint[];
};
export type TimelinePoint = {
  decision: number;
  phase: string;
  resources: Observation["state"]["resources"];
  progress: Observation["state"]["progress"];
  build: string[];
};
export type Episode = {
  episode_id: string;
  created_at: string;
  evidence_kind: string;
  deck: string;
  stake: string;
  branch: boolean;
  evaluation_eligible: boolean;
  fixture: string | null;
  agent?: string;
};
export type Bootstrap = {
  operator_token: string;
  config: any;
  workbench: boolean;
  paid_credentials: Record<string, boolean>;
};
export type RuntimeConnection = {
  ready: boolean;
  code: string | null;
  message: string;
};
export type RunInput = {
  agent: string;
  offline: boolean;
  preset: string;
  seed: string | null;
  cost_override?: 10 | "uncapped" | null;
  confirm_uncapped?: boolean;
};
export type ReviewOpenInput = {
  episode_id: string;
  retrospective?: boolean;
  prior_seed_exposure?: boolean;
};
export type AnnotationInput = {
  start_decision: number;
  end_decision: number;
  judgment: string;
  horizons_in_tension: string[];
  mechanism_summary: string;
  alternative_actions: string[];
  confidence: string;
  evidence_event_ids: string[];
  annotation_id: string | null;
};
export type DecisionExportFormat = "json" | "jsonl";

let operatorToken = "";

export function setOperatorToken(token: string) {
  operatorToken = token;
}

function explorerPath(path: string) {
  return `/explore${path}`;
}

async function request<T>(path: string, method = "GET", body?: unknown, reviewToken?: string, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  headers[reviewToken ? "X-Review-Token" : "X-BH-Operator"] = reviewToken ?? operatorToken;
  const response = await fetch("/api" + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.detail || "Request failed");
  return data as T;
}

export const bootstrap = () => request<Bootstrap>("/bootstrap");
export const listEpisodes = () => request<Episode[]>("/episodes");
export const restorePreview = (episodeId: string) => request<RestorePreview>("/operator/episodes/" + episodeId + "/restore");
export const restoreRun = (episodeId: string, input: { parent_head: string; plan_hash: string; authorize_paid: boolean; accept_compatible_update: boolean; confirm_uncapped?: boolean }) => request<{ episode_id: string }>("/operator/episodes/" + episodeId + "/restore", "POST", input);
export const budgetContinuationPreview = (episodeId: string) => request<BudgetContinuationPreview>("/operator/episodes/" + episodeId + "/continue-budget");
export const continueBudget = (episodeId: string, input: BudgetContinuationInput) => request<{ episode_id: string }>("/operator/episodes/" + episodeId + "/continue-budget", "POST", input);
export const startRun = (input: RunInput) => request<{ episode_id: string }>("/runs", "POST", input);
export const stopRun = () => request<{ stop_requested: boolean }>("/stop", "POST", {});
export const operatorStatus = () => request<any>("/operator/status");
export const operatorRuntime = (signal?: AbortSignal) => request<RuntimeConnection>("/operator/runtime", "GET", undefined, undefined, signal);
export const saveSettings = (input: unknown) => request<any>("/settings", "PUT", input);
export const openReview = (input: ReviewOpenInput) => request<{ review_token: string; view: View }>("/reviews", "POST", input);
export const openExplorer = (input: ReviewOpenInput) => request<{ review_token: string; view: View | null }>("/explore/sessions", "POST", { ...input, retrospective: true });
export const reviewView = (token: string) => request<View>("/review", "GET", undefined, token);
export const advanceReview = (token: string) => request<View>("/review/advance", "POST", {}, token);
export const listDecisions = (token: string) => request<DecisionLedger>(explorerPath("/decisions"), "GET", undefined, token);
export const decisionDetail = (token: string, decision: number) => request<View>(`${explorerPath("/decisions")}/${decision}`, "GET", undefined, token);
export const decisionTrace = (token: string, decision: number, signal?: AbortSignal) => request<any>(`${explorerPath("/decisions")}/${decision}/trace`, "GET", undefined, token, signal);
export const seekReview = (token: string, decision: number) => request<View>(explorerPath("/seek"), "POST", { decision }, token);
export const listAnnotations = (token: string, decision?: number) => request<any[]>(`${explorerPath("/annotations")}${decision === undefined ? "" : `?decision=${decision}`}`, "GET", undefined, token);
export const saveAnnotation = (token: string, input: AnnotationInput) => request<any>(explorerPath("/annotations"), "POST", input, token);
export const listReviewAnnotations = (token: string, decision?: number) => request<any[]>(`/review/annotations${decision === undefined ? "" : `?decision=${decision}`}`, "GET", undefined, token);
export const saveReviewAnnotation = (token: string, input: AnnotationInput) => request<any>("/review/annotations", "POST", input, token);
export type BranchCapability = { enabled: boolean; reason: string | null; requires_uncapped_confirmation: boolean };
export type BranchInput = {
  episode_id: string;
  decision: number;
  mode: "agent_continue" | "human_takeover" | "short_human_sequence" | "single_action_override";
  actions: Action[];
  confirm_uncapped?: boolean;
};
export const branchCapability = (token: string) => request<BranchCapability>("/review/branch-capability", "GET", undefined, token);
export const createBranch = (input: BranchInput) => request<{ episode_id: string }>("/branches", "POST", input);
export const compareBranch = (episodeId: string) => request<any>(`/operator/branches/${episodeId}/comparison`);
export const humanStatus = () => request<any>("/operator/human");
export const humanAction = (input: unknown) => request<{ queued: boolean }>("/operator/human", "POST", input);
export const listPanels = () => request<any[]>("/panels");
export const createPanel = (count: number) => request<any>("/panels", "POST", { count });
export const listBatches = () => request<any[]>("/batches");
export const createBatch = (input: unknown) => request<any>("/batches", "POST", input);
export const runBatch = (id: string, offline: boolean) => request<any>(`/batches/${id}/run`, "POST", { offline });
export const batchReport = (id: string) => request<any>(`/batches/${id}/report`);
export const batchExport = (id: string) => request<any>(`/batches/${id}/export`, "POST", {});

export async function download(path: string, filename: string, reviewToken?: string) {
  const response = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json", [reviewToken ? "X-Review-Token" : "X-BH-Operator"]: reviewToken ?? operatorToken },
  });
  if (!response.ok) throw new Error("Public export download failed");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  const disposition = response.headers.get("content-disposition");
  const suggested = disposition?.match(/filename="([^"]+)"/)?.[1];
  link.download = suggested || filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const downloadDecisionExport = (token: string, format: DecisionExportFormat) =>
  download(`${explorerPath("/export")}/${format}`, "decision-export." + format, token);
