import type { DecisionRow } from "./api";
import { cardLabel } from "./cardPresentation";

export function humanize(value: string) {
  return value.replaceAll("_", " ").toLowerCase();
}

export function number(value: string | number | null | undefined) {
  if (value == null) return "—";
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? numeric.toLocaleString("en-US")
    : String(value);
}

export function actionTitle(row: DecisionRow) {
  const item = cardLabel(row.item || "item", row.effects);
  switch (row.type) {
    case "play_hand":
      return `Play ${row.hand_types?.join(" / ") || "hand"}`;
    case "discard":
      return `Discard ${row.cards?.length ?? ""} cards`;
    case "buy":
      return `${row.mode === "buy_and_use" ? "Buy & use" : "Buy"} ${item}`;
    case "sell":
      return `Sell ${item}`;
    case "select_blind":
      return `Face ${item}`;
    case "skip_blind":
      return `Skip ${item}`;
    case "choose_pack":
      return `Choose ${item}`;
    case "use_consumable":
      return `Use ${item}`;
    case "reorder":
      return `Reorder ${row.area}`;
    default:
      return humanize(row.type).replace(/^./, (char) => char.toUpperCase());
  }
}

export function jokerChanges(row: DecisionRow, change: "added" | "removed") {
  const names =
    row[change === "added" ? "jokers_added" : "jokers_removed"] || [];
  const direct = change === "added" ? ["buy", "choose_pack"] : ["sell"];
  // A name-only change list cannot identify duplicate Jokers or indirect gains.
  // Use modifiers only when the action unambiguously names the single changed card.
  return names.length === 1 &&
    names[0] === row.item &&
    direct.includes(row.type)
    ? [cardLabel(names[0], row.effects)]
    : names;
}

export function actionResult(row: DecisionRow) {
  if (row.status === "awaiting_transition")
    return "Action committed · waiting for settled state";
  if (row.status === "in_progress") return "Action in progress";
  if (!row.action_number)
    return row.rejection_code
      ? "Rejected · no committed transition"
      : "No committed transition";
  if (row.type === "play_hand")
    return `+${number(row.score)} chips · ${number(row.total_chips)} / ${number(row.target_before)}`;
  if (row.type === "select_blind") return `Target ${number(row.target_after)}`;
  if (row.type === "skip_blind")
    return row.effects?.join(" · ") || "Blind skipped";
  if (row.type === "discard")
    return `${row.discards_after ?? "—"} discards remaining`;
  if (row.type === "reorder")
    return row.ordered_objects?.join(" → ") || "Order changed";
  if (row.money_after != null)
    return `$${number(row.money_before)} → $${number(row.money_after)}`;
  return "Action completed";
}

export const filters = [
  ["all", "All choices"],
  ["build", "Build & spending"],
  ["hands", "Hands & discards"],
  ["blinds", "Blinds & skips"],
  ["reorder", "Ordering"],
  ["uncommitted", "Uncommitted"],
] as const;

export function matchesFilter(row: DecisionRow, filter: string) {
  if (filter === "uncommitted") return !row.action_number;
  if (filter === "build")
    return [
      "buy",
      "sell",
      "choose_pack",
      "skip_pack",
      "use_consumable",
      "reroll_shop",
    ].includes(row.type);
  if (filter === "hands") return ["play_hand", "discard"].includes(row.type);
  if (filter === "blinds")
    return ["select_blind", "skip_blind", "reroll_boss"].includes(row.type);
  return filter === "all" || row.type === filter;
}
