export type DashboardTab = "runs" | "explore" | "review" | "human" | "batches" | "models" | "comparison";
export type DashboardRoute = { tab: DashboardTab; episodeId?: string; decision?: number };

const tabs = new Set<DashboardTab>(["runs", "explore", "review", "human", "batches", "models", "comparison"]);
export function readRoute(hash = window.location.hash): DashboardRoute {
  const [name, episodeId, rawDecision] = hash.replace(/^#/, "").split("/");
  if (!tabs.has(name as DashboardTab)) return { tab: "runs" };
  const tab = name as DashboardTab;
  if (!["explore", "review", "comparison"].includes(tab)) return { tab };
  if (!episodeId || !/^[a-f0-9]{32}$/.test(episodeId)) return { tab };
  const decision = rawDecision != null && /^\d+$/.test(rawDecision) ? Number(rawDecision) : undefined;
  return { tab, episodeId, ...(Number.isSafeInteger(decision) ? { decision } : {}) };
}
export function routeHash(route: DashboardRoute) {
  return "#" + route.tab + (route.episodeId ? "/" + route.episodeId : "") +
    (route.decision == null ? "" : "/" + route.decision);
}
export function routeKey(route: DashboardRoute) {
  return route.tab + (route.episodeId ? "/" + route.episodeId : "");
}
