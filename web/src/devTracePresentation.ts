import { cardLabel } from "./cardPresentation";
import { humanize } from "./decisionPresentation";

export type Data = Record<string, any>;
export type ObjectNames = Map<string | number, string>;
export type TraceEvent = {
  event_id: string;
  sequence: number;
  timestamp: string;
  type: string;
  payload: Data;
};
export type TraceTool = {
  call_id: string | null;
  name: string | null;
  arguments: unknown;
  raw_arguments: unknown;
  arguments_parse_error: boolean;
  delivered_results: {
    request_id: string;
    content: unknown;
    content_parse_error: boolean;
  }[];
};
export type TraceCall = {
  request_id: string;
  request_event: TraceEvent;
  context_event_id: string | null;
  response_event: TraceEvent | null;
  error_event: TraceEvent | null;
  status: string;
  tools: TraceTool[];
  journal_events: TraceEvent[];
};
export type Trace = {
  decision: number;
  complete: boolean;
  calls: TraceCall[];
  events: TraceEvent[];
  observation: unknown;
  transition: unknown;
  omissions: string[];
  linkage: string;
};
export function record(value: unknown): Data {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Data)
    : {};
}
export function title(value: string) {
  return humanize(value).replace(/^./, (c) => c.toUpperCase());
}
export function gameMoney(value: unknown) {
  return typeof value === "number" ||
    (typeof value === "string" && value.trim())
    ? Number.isFinite(Number(value))
      ? `$${Number(value).toLocaleString("en-US")}`
      : "Unknown"
    : "Unknown";
}
function objectId(value: unknown): value is string | number {
  return typeof value === "string" ||
    (typeof value === "number" && Number.isSafeInteger(value) && value > 0);
}
export function recordedModelContext(body: unknown): Data | null {
  const request = record(body);
  const messages = request.input ?? request.messages;
  if (!Array.isArray(messages)) return null;
  const content = messages.find((item) => record(item).role === "user")?.content;
  if (typeof content !== "string") return null;
  try {
    const context = record(JSON.parse(content));
    return Object.keys(record(context.observation)).length ? context : null;
  } catch {
    return null;
  }
}
export function objectNames(observation: unknown): ObjectNames {
  const state = record(record(observation).state);
  const names: ObjectNames = new Map();
  for (const area of [
    "hand",
    "jokers",
    "consumables",
    "offers",
    "revealed_blinds",
  ]) {
    for (const item of Array.isArray(state[area]) ? state[area] : []) {
      const obj = record(item);
      if (!objectId(obj.id)) continue;
      names.set(
        obj.id,
        obj.face_down
          ? "Face-down card"
          : cardLabel(
              typeof obj.label === "string" ? obj.label : String(obj.id),
              Array.isArray(obj.effects)
                ? obj.effects.filter((x: unknown) => typeof x === "string")
                : [],
            ),
      );
    }
  }
  return names;
}
export function toolTitle(tool: TraceTool, names: ObjectNames) {
  const args = record(tool.arguments);
  const name = tool.name || "unnamed_tool";
  if (tool.arguments_parse_error) return title(name);
  const label = (id: unknown) =>
    objectId(id) ? names.get(id) || String(id) : "item not recorded";
  const cards = (ids: unknown) =>
    Array.isArray(ids) ? ids.map(label).join(", ") : "cards not recorded";
  switch (name) {
    case "play_hand":
      return `Play ${cards(args.card_ids)}`;
    case "discard":
      return `Discard ${cards(args.card_ids)}`;
    case "buy":
      return `${args.mode === "buy_and_use" ? "Buy and use" : "Buy"} ${label(args.offer_id)}`;
    case "sell":
      return `Sell ${label(args.owned_id)}`;
    case "choose_pack":
      return `Choose ${label(args.offer_id)} from pack`;
    case "use_consumable":
      return `Use ${label(args.consumable_id)}`;
    case "select_blind":
      return `Face ${label(args.blind_id)}`;
    case "skip_blind":
      return `Skip ${label(args.blind_id)}`;
    case "inspect_state":
    case "inspect_page":
      return `Inspect ${humanize(String(args.section || args.page || "public state"))}`;
    case "read_skill":
      return `Read skill: ${args.name ?? "not recorded"}`;
    case "read_rules":
      return `Read rules: ${args.key ?? "not recorded"}`;
    case "calculate":
      return `Calculate ${args.expression ?? "expression not recorded"}`;
    case "set_run_note":
      return `Write notebook: ${args.key ?? "key not recorded"}`;
    case "delete_run_note":
      return `Delete notebook entry: ${args.key ?? "key not recorded"}`;
    case "retrieve_action_result":
      return `Read action result: ${args.decision_id == null ? "latest" : `decision ${Number(args.decision_id) + 1}`}`;
    default:
      return title(name);
  }
}
export function quotedPrice(tool: TraceTool, context: Data): string | null {
  const costs = record(context.current_costs);
  const args = record(tool.arguments);
  if (tool.arguments_parse_error) return null;
  if (tool.name === "reroll_shop" || tool.name === "reroll_boss") {
    const quote = record(record(costs.rerolls)[tool.name]);
    return `Quoted upfront cost: ${gameMoney(quote.cash_cost)}`;
  }
  if (tool.name === "buy" || tool.name === "choose_pack") {
    const offer = (Array.isArray(costs.offers) ? costs.offers : []).find(
      (x: Data) => x.offer_id === args.offer_id,
    );
    return `Quoted upfront cost: ${gameMoney(offer?.cash_cost)}`;
  }
  if (tool.name === "sell") {
    const item = (
      Array.isArray(costs.owned_items) ? costs.owned_items : []
    ).find((x: Data) => x.owned_id === args.owned_id);
    return `Quoted sale proceeds: ${gameMoney(item?.quoted_sale_proceeds)}`;
  }
  return null;
}
export function statusText(status: string) {
  return (
    (
      {
        waiting_for_response: "Waiting for model response",
        response_received: "Model responded; execution not confirmed",
        transport_error: "Provider request failed",
        helper_result: "Helper result recorded",
        action_commit: "Game action committed",
        action_rejected: "Request rejected",
        harness_failure: "Harness failure",
        no_recorded_response: "Request ended without a recorded response",
      } as Record<string, string>
    )[status] || title(status)
  );
}
export function resourceChanges(before: unknown, after: unknown) {
  const a = record(record(record(before).state).resources);
  const b = record(record(record(after).state).resources);
  return ["money", "hands", "discards", "chips"].flatMap((key) => {
    if (a[key] == null || b[key] == null) return [];
    const before = key === "money" ? gameMoney(a[key]) : String(a[key]);
    const after = key === "money" ? gameMoney(b[key]) : String(b[key]);
    return before === after
      ? []
      : [{ label: key === "money" ? "Cash" : title(key), before, after }];
  });
}
