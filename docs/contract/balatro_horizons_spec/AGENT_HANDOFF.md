# Coding-agent handoff

Read `BALATRO_HORIZONS_SPEC.md` as the implementation contract. This file and the full specification are for the **coding agent and evaluator**, not the model playing Balatro. The playing model receives only the approved gameplay prompt, public observations and permitted tools.

## Task

Implement Balatro Horizons: a local-first full-run LLM evaluation harness and an expert run-review workbench. The owner is an experienced Balatro player who will inspect decisions and investigate conflicts among immediate scoring, near-term survival and long-term development.

Start by inspecting the repository and execution environment. Reuse a suitable existing stack where present. Prefer a thin, audited BalatroBot adapter over a new simulator. Create the milestone-zero capability/visibility audit and environment manifest, then implement the milestones in order. Make reasonable decisions under the specification's defaults; document deviations in architecture decision records rather than stopping for minor preference questions.

The first working deliverable is an offline vertical slice: a deterministic test episode flows through validated observations/actions, an append-only trace, a chronological browser viewer and an annotation. Then connect the real game, certify replay/checkpoints, implement interventions, and complete the model/batch/reporting path.

## Non-negotiables

Preserve complete runs and native strategic decisions. Do not auto-select blinds, discard strategic choices, forward hidden engine state, expose seeds or debug tools to the playing model, silently repair model actions, or include human-assisted branches in autonomous scores.

Record exact agent-visible inputs and committed actions. A lost run, an invalid action, spent budgets and an infrastructure failure are different outcomes. Keep original runs immutable. Keep annotations and interventions separate from claims of optimal action or causal attribution.

Do not build synthetic strategy puzzles, modified mechanics, a solver or a public leaderboard in this version. Do not invent benchmark results. A saved file is not proof of deterministic restoration; test it against actual native continuations.

No paid API calls without explicit operator authorization and episode/batch caps. Do not overwrite personal game saves, install proprietary game files, or expose credentials. Without a licensed local game or keys, finish all possible offline components and tests and report live validation as blocked—not passed.

## Completion report

Deliver the working repository and setup/run commands. Report which milestones were implemented, commands actually executed, test pass/fail/skip counts, live versus synthetic evidence locations, dependency pins, remaining action/visibility/checkpoint gaps, spending if authorized, and precise external prerequisites still needed.

Run the tests; do not deliver only a plan or scaffold. Do not claim scientific readiness until the live fidelity, information-boundary and action-coverage gates pass.
