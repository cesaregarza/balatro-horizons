"""Precommitted slots, complete attempt accounting, seed-cluster uncertainty."""

import random
import secrets
import uuid
from collections import Counter, defaultdict

from balatro_horizons.cost_limits import require_capped_defaults
from balatro_horizons.storage.journal import atomic_json, digest

VALID = {"WIN", "GAME_LOSS", "AGENT_PROTOCOL_FAILURE", "AGENT_ABORT", "BUDGET_EXHAUSTED"}


def seed_panel(path, count=20):
    values = [
        "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
        for _ in range(count)
    ]
    atomic_json(path, {"split": "development", "seeds": values}, immutable=True)
    return {"count": count, "commitment": digest(values)}


def plan_batch(store, config, panel, agents, replicates=2):
    require_capped_defaults(config.budgets.max_episode_cost_usd, config.budgets.max_batch_cost_usd)
    if not agents or len(set(agents)) != len(agents) or replicates < 1 or replicates > 10:
        raise ValueError("INVALID_BATCH_DIMENSIONS")
    if "human" in agents:
        raise ValueError("MANUAL_RUNS_NOT_BATCH_AGENTS")
    if set(agents) - ({"heuristic", "random_legal"} | set(config.models)):
        raise ValueError("UNKNOWN_AGENT_CONFIGURATION")
    seeds = panel["seeds"]
    if len(seeds) != len(set(seeds)) or not seeds:
        raise ValueError("INVALID_SEED_PANEL")
    bid = uuid.uuid4().hex
    groups = [uuid.uuid4().hex for _ in seeds]
    slots = [
        {"slot_id": uuid.uuid4().hex, "seed_group": group, "replicate": replicate, "agent": agent}
        for group in groups
        for replicate in range(replicates)
        for agent in agents
    ]
    public = {
        "batch_id": bid,
        "config": config.public(),
        "config_hash": digest(config.model_dump()),
        "seed_commitment": digest(seeds),
        "split": panel.get("split", "development"),
        "slots": slots,
        "max_infrastructure_replacements": 1,
    }
    atomic_json(store.root / "batches" / bid / "plan.json", public, immutable=True)
    atomic_json(
        store.root / "batches" / bid / "private.json",
        {"seed_by_group": dict(zip(groups, seeds, strict=True))},
        immutable=True,
    )
    return public


def summarize(plan, attempts):
    by_slot = defaultdict(list)
    for attempt in attempts:
        manifest = attempt["manifest"]
        if (
            manifest.get("parent_episode_id")
            or manifest.get("assistance")
            or manifest.get("agent") == "human"
            or not manifest.get("evaluation_eligible", False)
        ):
            continue
        if manifest.get("batch_id") == plan["batch_id"]:
            by_slot[manifest["slot_id"]].append(attempt)
    result = {}
    for agent in sorted({s["agent"] for s in plan["slots"]}):
        slots = [s for s in plan["slots"] if s["agent"] == agent]
        selected = []
        all_attempts = []
        costs = 0.0
        status = Counter()
        clusters = defaultdict(list)
        for slot in slots:
            runs = sorted(by_slot[slot["slot_id"]], key=lambda r: r["manifest"]["created_at"])
            all_attempts.extend(runs)
            costs += sum((r["summary"] or {}).get("cost_usd", 0) for r in runs)
            status.update((r["summary"] or {}).get("outcome", "RUNNING") for r in runs)
            valid = next(
                (r for r in runs if r["summary"] and r["summary"]["outcome"] in VALID), None
            )
            if valid:
                win = int(valid["summary"]["outcome"] == "WIN")
                selected.append({**slot, "win": win, "episode_id": valid["episode_id"]})
                clusters[slot["seed_group"]].append(win)
        n, v, w = len(slots), len(selected), sum(s["win"] for s in selected)
        result[agent] = {
            "planned": n,
            "attempts": len(all_attempts),
            "valid": v,
            "wins": w,
            "unresolved": n - v,
            "win_rate": w / v if v else None,
            "coverage": v / n if n else None,
            "missing_outcome_bounds": [w / n, (w + n - v) / n] if n else None,
            "attempt_outcomes": dict(status),
            "all_attempt_cost_usd": costs,
            "seed_bootstrap_ci": cluster_interval(clusters),
            "seed_bootstrap_diagnostic": cluster_diagnostic(clusters),
            "selected_slots": selected,
        }
    comparisons = []
    agents = list(result)
    for i, left in enumerate(agents):
        for right in agents[i + 1 :]:
            a = {
                (s["seed_group"], s["replicate"]): s["win"] for s in result[left]["selected_slots"]
            }
            b = {
                (s["seed_group"], s["replicate"]): s["win"] for s in result[right]["selected_slots"]
            }
            pairs = set(a) & set(b)
            differences = defaultdict(list)
            for seed, rep in sorted(pairs):
                differences[seed].append(a[seed, rep] - b[seed, rep])
            comparisons.append(
                {
                    "left": left,
                    "right": right,
                    "matched_pairs": len(pairs),
                    "missing_pairs": len(
                        {
                            (s["seed_group"], s["replicate"])
                            for s in plan["slots"]
                            if s["agent"] in (left, right)
                        }
                    )
                    - len(pairs),
                    "difference": sum(a[p] - b[p] for p in pairs) / len(pairs) if pairs else None,
                    "seed_bootstrap_ci": cluster_interval(differences),
                    "seed_bootstrap_diagnostic": cluster_diagnostic(differences),
                }
            )
    return {
        "batch_id": plan["batch_id"],
        "evidence_kinds": sorted(
            {
                r["manifest"].get("evidence_kind", "UNSPECIFIED")
                for r in attempts
                if r["manifest"].get("batch_id") == plan["batch_id"]
            }
        ),
        "agents": result,
        "paired_comparisons": comparisons,
        "bootstrap": {"unit": "seed", "draws": 10000, "analysis_rng": 0},
        "branches_excluded": True,
    }


def cluster_diagnostic(clusters):
    if len(clusters) < 2:
        return {"status": "insufficient_seed_clusters", "seed_clusters": len(clusters)}
    means = {sum(group) / len(group) for group in clusters.values()}
    if len(means) == 1:
        return {"status": "degenerate", "seed_clusters": len(clusters),
                "caution": "No observed variation between seed means; a collapsed bootstrap interval does not establish certainty."}
    return {"status": "varying_seed_means", "seed_clusters": len(clusters)}


def cluster_interval(clusters, draws=10000):
    if len(clusters) < 2:
        return None
    groups = [clusters[k] for k in sorted(clusters)]
    rng = random.Random(0)
    estimates = []
    for _ in range(draws):
        sample = [groups[rng.randrange(len(groups))] for _ in groups]
        estimates.append(sum(sum(g) for g in sample) / sum(len(g) for g in sample))
    estimates.sort()
    return [estimates[int(draws * 0.025)], estimates[min(draws - 1, int(draws * 0.975))]]
