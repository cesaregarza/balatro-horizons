# Balatro Horizons
## Agent implementation specification — v0.1

**Status:** Proposed build contract; no implementation or live-game validation is implied.  
**Prepared:** 2026-09-14.  
**Working repository name:** `balatro-horizons`. This is a distinct project, not a claim of affiliation with the existing BalatroBench. [S1]  
**Audience:** A coding agent implementing a local-first benchmark and expert review workbench.  
**Requirement language:** MUST is required for the indicated milestone; SHOULD permits a documented alternative; MAY is optional.

---

## 1. Mission and research boundary

Build a system that lets language-model agents play complete, ordinary Balatro runs, records the decisions they make, and lets a strong human player investigate how those decisions affect immediate survival and later opportunities.

The central question is:

> Can an agent coordinate immediate scoring, near-term survival, and long-term development when its own decisions change the situations it will encounter later?

Full-run autonomous success is the primary performance outcome. Expert review and checkpoint interventions are diagnostic evidence, not substitutes for autonomous success and not an oracle for the optimal move.

The project owner reports Completionist++ experience and will provide domain review. Treat this as user-provided context, not a verified external credential. Make that review efficient rather than attempting to automate strategic judgment in v0.

### 1.1 Hypotheses, not assumptions to bake into scoring

The benchmark is motivated by three hypotheses: strong players can win reliably under selected conditions; familiar mechanics still produce unfamiliar, path-dependent decision contexts; and some model failures involve conflicts among horizons rather than rule ignorance. Measure these. Do not assume every seed is winnable, that all losses are mistakes, that Balatro is absent from model training, or that playing well proves a particular internal reasoning process.

“Longer-term” is not synonymous with “better.” Spending everything to survive can be correct. Saving money, scaling, and preserving options receive no automatic reward.

### 1.2 First usable product

The owner can run a model on a seed, open the resulting run in a browser, inspect exactly what was available at each decision, annotate a decision or an interval, restore a verified checkpoint, substitute an action or take over, and compare the resulting branch without overwriting the original run.

### 1.3 Non-goals for v0

Do not build modified rules, synthetic strategy puzzles, reinforcement-learning training, an optimal solver, automatic causal blame assignment, an elaborate leaderboard, computer-vision control, or a replacement game engine. Do not optimize provider-specific strategies. Do not ship game binaries, extracted proprietary assets, purchased game files, credentials, or private seed manifests.

Build full runs first. Derive future diagnostic fixtures from observed failures, rather than replacing full runs with hand-authored puzzles.

## 2. Concrete defaults and scope

These are implementation defaults, not empirical claims about the best experimental design.

| Setting | Initial choice |
|---|---|
| Objective | Win the ordinary run at the configured native win condition; initial target Ante 8. Stop at the verified win event, not an inferred round count. |
| Smoke configuration | Red Deck / White Stake, to test plumbing. |
| Strategic pilot configuration | Yellow Deck / Gold Stake, provisional until human calibration. Do not pool with the smoke configuration. |
| Profile | Dedicated, frozen, fully unlocked profile; isolate it from the owner's personal save. |
| Mechanics | Ordinary game mechanics; only pinned observation/control instrumentation allowed. |
| Agent interface | Structured, player-equivalent observations; no screenshots in the primary model track. |
| Attempts | One uninterrupted attempt per seed/replicate; no model-controlled restart or rewind. |
| Information | Public game state and public history, without seed, RNG state, hidden identities/order, or future outcomes. |
| External help | Frozen rules reference and restricted arithmetic tool; no web, shell, filesystem, or simulation access. |
| Human help | None in scored autonomous runs; intervention branches are separately labeled diagnostics. |
| Pilot size | 20 development seeds, 2 independent model replicates per seed, initially 2 configured model IDs. This is an exploratory batch, not a powered final study. |
| Human comparison | First-attempt human runs under the same configuration and information policy; track prior seed exposure. |
| Execution | Local-first, one game process per worker; concurrency 1 by default. |
| Paid calls | Disabled unless the operator explicitly enables them and provides spending ceilings. |

The owner may change deck/stake and budgets during development. Freeze them before a comparative batch. Never silently adjust difficulty after inspecting test performance. Do not discard seeds because an agent or a human lost them.

## 3. Upstream reuse and the first engineering audit

### 3.1 What has been checked

BalatroBot is an existing Balatro mod exposing a JSON-RPC HTTP interface. [S2] Its documentation includes seeded starts, state reads, gameplay controls, save/load, and evaluator-like debugging methods. Its state schema also includes the run seed. These are capabilities to audit, not permission to forward the whole interface. [S3]

The documented installation uses a purchased Balatro installation, Lovely, Steamodded, and the BalatroBot tooling. Resolve and pin compatible versions on the actual host rather than treating a floating “latest” documentation page as an environment lock. [S4]

The inspected BalatroLLM loop automatically selects blinds instead of offering the model the play/skip choice. Reusing it unchanged would narrow the action space. [S5]

These observations were checked against public sources on 2026-09-14. No game installation, save fidelity, platform compatibility, or complete native action coverage has been tested for this specification.

### 3.2 Reuse decision

Prefer a thin adapter around BalatroBot. Borrow upstream clients or logging utilities where their licenses and behavior permit. Do not inherit a game loop without auditing its strategic choices. Do not begin by implementing another simulator.

### 3.3 Milestone-zero deliverables

Create `docs/capability_audit.md`, `docs/visibility_policy.md`, and an immutable resolved environment manifest. The capability audit MUST list each required interaction, its observed native behavior, the adapter mapping, the upstream revision, and live validation evidence or a blocker.

Audit at least: play, discard, blind select/skip, buy, buy-and-use where natively available, sell, consumable use and targeting, shop reroll, leaving the shop, pack selection/skip with multiple selections, ordering hand/Jokers/consumables, native boss reroll when available, and actions available outside the shop.

A missing upstream action MUST NOT become a silently unavailable strategy. Add a narrow, tested adapter/mod extension, or mark the configuration blocked. For native actions that depend on phase or special effects, test the actual pinned game; do not guess from a generic action list.

Also audit save/load by phase, RNG preservation, pending effects, current Joker counters, profile isolation, seed replay, terminal detection, and whether instrumentation changes mechanics. A successful save RPC alone is not checkpoint-fidelity evidence.

### 3.4 Unavailable dependencies

Without a local game installation or credentials, continue with the schemas, fake adapter, deterministic replay adapter, runner, review UI, and offline tests. Mark live requirements `BLOCKED_EXTERNAL_DEPENDENCY`. Never fabricate live results, mark skipped live tests as passing, or quietly substitute a simulator. Emit precise machine-readable blockers and setup instructions.

## 4. Architecture and trust boundaries

Use Python 3.12+, typed models/JSON Schema, a local HTTP service, SQLite as a rebuildable index, append-only JSONL as the source of truth, and a small browser UI. Default implementation: Pydantic, FastAPI, React, TypeScript, and Vite; lock resolved dependency versions. An existing repository's well-supported equivalent stack may be retained with an architecture decision record.

```text
Model provider / human controller / baseline policy
                         |
               AgentGateway (public contract only)
                         |
       Runner + budget accounting + event journal
                         |
          ObservationFilter / ActionValidator
                         |
              NativeGameAdapter (private)
                         |
              Pinned Balatro + instrumentation

Review UI -> Review API -> public trace + annotations
Evaluator -> CheckpointManager -> private saves / branch runner
Reporter  -> immutable trace / manifests / terminal records
```

The model gets capabilities through the gateway, not credentials or raw engine access. Save/load, restart, debug state mutation, screenshots with privileged overlays, private manifests, and evaluator notes remain outside this boundary. Bind private services to loopback and use an internal token. Human review and evaluator routes MUST be inaccessible to the model gateway.

### 4.1 Required modules

| Module | Responsibility |
|---|---|
| `engine` | Native, fake, and replay adapter implementations; readiness and terminal detection. |
| `observations` | Allowlisted projection, visibility-aware IDs, public history, canonical serialization. |
| `actions` | Discriminated action types, native mappings, legality and stale-state checks. |
| `agents` | Provider-neutral policy interface, human input, baselines, provider adapters. |
| `runner` | Episode loop, budgets, request journal, explicit failure classification. |
| `storage` | Immutable traces, hashes, private checkpoints, derived SQLite index. |
| `review` | Chronological reader, annotations, exposure tracking, branch controls. |
| `evaluation` | Seed manifests, batch scheduling, accounting, diagnostic and outcome reports. |

The adapter MUST expose separate public and evaluator interfaces. A useful target is `observe_public()`, `apply_public_action()`, `wait_ready()`, and `terminal_status()` on the gameplay side; `start_run()`, `checkpoint()`, `restore()`, and private inspection on the evaluator side. Names are proposed local interfaces, not claims about upstream method names.

## 5. Public observation contract

### 5.1 Principle

Expose facts a player can know under the frozen information policy, not every field in the engine. Implement an allowlist with provenance/visibility rules. Never sanitize by deleting just a few known-sensitive keys.

Required observation groups:

| Group | Contents |
|---|---|
| Identity | Schema version, opaque public episode ID, monotonically increasing observation ID, public-state hash. |
| Progress | Current phase, displayed ante/blind, native public progress and terminal indicators. |
| Resources | Money, hands/discards, capacities, visible costs and active public resource modifiers. |
| Cards | Ordered hand with only visible attributes; ordered owned Jokers/consumables, visible effects, modifiers and counters. |
| Scoring facts | Public hand levels/base values, current chips/target, displayed mechanics. Not a computed best move. |
| Challenges | Current and natively revealed upcoming blinds, visible tags and effects; no unrevealed future bosses. |
| Shop/pack | Only current revealed offers, prices, remaining choices, targets and capacities. Unopened pack contents remain hidden. |
| Persistent state | Public vouchers, tags, deck changes and other visible persistent effects. |
| Deck knowledge | Public composition or history-derived counts when justified, with exact/uncertain provenance; never live draw order. |
| Action surface | Available action types, visible argument constraints and target handles; no strategic rankings. |
| Context | Objective, remaining harness budgets, recent public events, agent-authored memory. |

Use explicit `null`/unknown states rather than converting unavailable facts to zero or false. Inconsistent visible facts MUST cause a validation error rather than plausible invented values. Preserve negative money, dynamic limits, repeated ante values, and large numeric values without lossy coercion; use documented decimal/scientific string encoding where frontend number precision is insufficient.

### 5.2 Hidden information and identity

Never expose the seed, RNG state, engine pointers, hidden card keys/ranks/suits, unrevealed outcomes, internal queue contents, or sorted/unsorted private draw arrays. Public run IDs and filenames MUST NOT contain seeds.

Public handles MUST encode no rank, suit, seed, future position, or private object ID. Stable handles are allowed only while identity is publicly trackable. After a concealed shuffle or other loss of trackability, issue new handles and discard the private-to-public linkage from model-visible history. Face-down cards and shuffled/face-down Jokers require explicit tests. Preserve legitimately remembered public history; do not reveal which previously known object a newly obscured slot contains.

Deck summaries MUST be computed from authorized public information. Do not query hidden truth to resolve uncertainty caused by concealed identity or a hidden transformation. Distinguish initial composition, known changes, observed draws and uncertain remaining composition.

Current effect descriptions and counters MUST match public game behavior for the pinned version. Do not replace dynamically changing effects with static catalog text. Where text and structured fields disagree, block and fix the adapter.

### 5.3 Legal actions must not become an oracle

Provide action schemas and visible constraints, not a ranked or pruned strategic menu. Do not call the engine speculatively to discover hidden-dependent legality. A native rejection whose text reveals hidden state must be replaced with a safe generic failure. A combinatorial action space need not be exhaustively enumerated.

Audit any displayed local score estimate against the chosen interface policy. The primary track MUST NOT add an exact score evaluator or a hand optimizer. Such tools belong to a separately named assistance track if implemented later.

### 5.4 Illustrative envelope

This example specifies shape only; it is not a captured game state. The complete emitted schema MUST contain the groups above and reject unknown fields at public boundaries.

```json
{
  "schema_version": "1.0",
  "episode_id": "ep_opaque_001",
  "observation_id": 42,
  "public_state_hash": "sha256:<canonical-public-state>",
  "objective": {"kind": "native_run_win", "target_ante": 8},
  "phase": "SHOP",
  "state": {
    "progress": {},
    "resources": {},
    "hand": [],
    "jokers": [],
    "consumables": [],
    "revealed_blinds": [],
    "offers": [],
    "public_deck_knowledge": {}
  },
  "available_action_types": ["buy", "leave_shop"],
  "action_constraints": {},
  "recent_public_events": [],
  "memory": "",
  "remaining_budget": {}
}
```

The public-state hash covers canonical observable game facts and public handles, excluding timestamps, observation counters, logs, budget counters and agent memory. It is not proof that private engine states are equal. Keep private checkpoint fingerprints separately.

## 6. Action contract and execution semantics

### 6.1 Action families

Define strict discriminated unions rather than an arbitrary `method` plus unvalidated arguments. The following are local names; map them to verified native operations.

| Action | Essential arguments |
|---|---|
| `select_blind`, `skip_blind` | Current public blind handle when needed. |
| `play_hand`, `discard` | Selected public card handles; order policy explicitly documented. |
| `reorder` | Area plus complete ordered permutation of its public handles. |
| `buy` | Offer handle and native acquisition mode where relevant. |
| `sell` | Owned object handle. |
| `use_consumable` | Owned handle and optional public targets. |
| `reroll_shop`, `reroll_boss` | Only available under verified native conditions. |
| `choose_pack` | Offer handle and optional targets. |
| `skip_pack` | Skip remaining choices, only when natively permitted. |
| `leave_shop` | No strategy chosen by the harness. |
| `cash_out` | Explicit if relevant; only eligible for automation after the audit below. |

An agent response contains one operation: a game action, rules lookup, public history read, or arithmetic request. A game-action envelope is:

```json
{
  "observation_id": 42,
  "action": {"type": "buy", "offer_id": "offer_opaque_7"},
  "memory_update": null,
  "decision_note": null
}
```

`memory_update` replaces bounded agent-authored notes when supplied. `decision_note` is an optional short decision summary, not a demand for hidden reasoning and never a scored field. The default prompt does not require explanations. Any mandatory planning scaffold is a separately labeled condition.

### 6.2 Validate, serialize, settle

Check schema, phase, observation ID, public handles, visible affordability/capacity constraints and targets. Resolve handles to current native indices only inside the adapter. Do not auto-correct, auto-select targets, auto-buy, auto-sell or pick a fallback play.

Allow only one in-flight mutation per game. Append an action-intent record before mutation, then an acknowledgment/result record after it. Wait until the pinned engine is at a validated decision boundary with relevant effects settled. Identical public snapshots alone are insufficient evidence that the engine queue is settled.

A network timeout after submission is ambiguous: the action may have executed. Never blindly retry a mutation. Recover using an engine-side request-ID journal or a validated transactional checkpoint/replay protocol. When execution cannot be established, stop as infrastructure failure and preserve evidence. Do not claim exactly-once execution without an implemented mechanism.

Invalid actions do not mutate the game. Return a sanitized error and allow correction under the fixed retry policy. Count every attempt and provider call. Reset the consecutive-invalid counter only after a committed game action, not after helper calls. Harness-caused stale observations are infrastructure errors; fabricated/stale IDs from an otherwise correct agent input are protocol errors.

### 6.3 Automation boundary

The model MUST control blind play/skip, shop exit, pack skip, purchases, use/sell actions and ordering whenever strategically meaningful. A one-button confirmation may be automated only after the phase audit establishes that no available player action or timing choice is removed. Log every automated transition as `actor=harness`.

Do not automatically cash out merely because a predecessor implementation does. Check whether any legal pre-transition use/sell/reorder choice is relevant in the pinned game. Fail closed on an unsupported phase rather than taking a plausible default action.

## 7. Agent harness, memory and resource limits

### 7.1 One shared harness

Implement `AgentPolicy.decide(public_context) -> operation` for model, human, replay and baseline controllers. Provider adapters translate transport formats only; they MUST NOT add model-specific strategy advice or repair decisions.

Ship a fake provider for deterministic tests and at least one real provider adapter. Two configured model IDs can share a provider. Resolve actual provider request/usage semantics from its official documentation during implementation. Unsupported settings must fail validation, not silently disappear.

Persist the exact system prompt, rendered requests, tool schemas, available provider responses, model identifier returned by the provider, requested identifier, reasoning/temperature settings, context limits and usage metadata. Do not claim reproducibility of an opaque model's hidden internals.

### 7.2 Default prompt

```text
You are playing one Balatro run. Your objective is to maximize the chance of
winning the configured run within the available interaction and inference
budgets. The observation and tools define the information available to you.
Choose your own strategy. Resources and intermediate scores are not separate
objectives. You may consult the provided rules and public history and use the
arithmetic tool. You cannot restart, inspect hidden state, or access the future.
Return exactly one permitted operation using the current observation ID.
You may maintain bounded notes for your own future decisions. Explanations
are optional and are not graded.
```

### 7.3 Information and memory defaults

Each decision starts with the same objective/rules kernel, the complete current public observation, the most recent 20 canonical public events, and up to 4,096 Unicode characters of agent-authored memory. Construct fresh requests rather than relying on undisclosed provider conversation state. Retain helper-tool exchanges within the current decision.

Provide `rules.lookup` over a frozen, versioned mechanics reference, `history.read` over past public events only, and `arithmetic` over a bounded arithmetic expression grammar. Rules content may explain mechanics but MUST NOT contain strategy guides, seed solutions, benchmark labels, or test-run traces. Generate locally from permitted sources when redistribution rights are uncertain.

The arithmetic tool MUST NOT execute Python, filesystem operations, imports, network requests, game calls, or simulation. Use a restricted parser, finite operations and magnitude/output limits. Do not use general `eval`.

Use deterministic pagination and record all tool results. If the request exceeds the configured input limit, remove oldest optional recent-event entries according to a fixed policy and record the omission; they remain retrievable through history. Never silently truncate current observations, rules kernel, tool definitions or retained memory. If these alone do not fit, terminate with a harness/configuration failure.

Reject oversized memory updates with a safe validation error; do not silently rewrite the agent's notes. Count rules, history and arithmetic requests against helper and provider-call budgets. There is no automatic strategic summarizer.

### 7.4 Default limits

| Limit | Default |
|---|---:|
| Maximum committed game actions per episode | 1,500 |
| Maximum provider calls per episode, including corrections/helpers | 2,000 |
| Maximum helper calls per game decision | 8 |
| Maximum provider input tokens per call | 32,768 |
| Maximum provider output tokens per call | 8,192, including reasoning tokens where provider semantics permit |
| Consecutive invalid game-action submissions | 3 |
| Maximum provider transport attempts for a request | 3 total; all attempts logged |
| Concurrent game workers | 1 |

These are pilot engineering ceilings. Record native token counts, cached-token counts and reported reasoning tokens separately where available; unknown values remain unknown. Equal numeric token ceilings do not imply identical compute across providers. Record serialized input size as an additional provider-independent diagnostic.

Provider/network timeouts and engine-settling timeouts are configurable operational safeguards, not clocks shown as game time. Record their values. A spent inference/action limit is `BUDGET_EXHAUSTED`; an unavailable external service is infrastructure failure. Use no automatic arbitrary move when a budget is exhausted.

Paid mode requires explicit episode and batch dollar ceilings plus a dated pricing configuration. Reserve a conservative upper-bound charge before a request where pricing supports it; otherwise refuse paid execution until a safe ceiling mechanism is configured. Preserve reported usage even when dollar cost cannot be estimated. Do not invent prices or silently exceed a cap. Offline fake/replay mode must work without keys and without network calls.

## 8. Event journal, storage and reproducibility

### 8.1 Sources of truth

Write append-only JSONL for events and immutable manifests. SQLite indexes these for review and can be rebuilt. Never make a mutable aggregate spreadsheet or database row the only evidence for a run.

Suggested layout:

```text
data/
  public_runs/<episode_id>/
    manifest.public.json
    events.jsonl
    observations/<observation_id>.json
    agent_io/<request_id>.json
    summary.json
  private_runs/<episode_id>/
    manifest.private.json
    engine_events.jsonl
    checkpoints/<checkpoint_id>/...
    checkpoint_index.jsonl
  annotations/<episode_id>.jsonl
  batches/<batch_id>/...
  index.sqlite
```

Private data must be outside the public static root. The primary model has access to neither directory directly. Review APIs enforce their own temporal restrictions even when serving the public trace.

### 8.2 Event fields

Every event MUST include `schema_version`, `episode_id`, `event_id`, `sequence`, `event_type`, UTC timestamp, actor, relevant `observation_id`, request/transaction ID, payload, previous-event hash and event hash. Use deterministic canonical serialization for hashable payloads. Hash chains detect changes relative to a trusted recorded head; they are not proof of authenticity by themselves.

Required event types cover episode start, observation, provider request/response, helper request/result, action intent/commit/rejection/unknown status, automatic transition, checkpoint result, budget update, error, intervention and terminal result. Log provider retries independently.

Preserve before/after public observations and ordered actions. Deterministic resource deltas may be computed for review; label them as observed changes, not strategic evaluations. Keep sensitive engine responses private and sanitize errors before they enter public logs. Never record API secrets or authorization headers.

The journal MUST distinguish proposed actions from committed actions. After a crash, recover an incomplete record explicitly; do not infer that a missing acknowledgment means nothing happened. Use atomic writes and a documented durability policy.

### 8.3 Manifest fields

Record benchmark/schema version; repository commit and dirty-tree hash; OS/runtime; game and mod versions/checksums; adapter patch; profile hash and unlock description; rules hash; visibility and action-policy versions; objective; deck/stake; seed-manifest commitment; seed alias; exact model/provider/harness settings; budgets; parent/branch IDs; timestamps; and test-certification IDs.

Actual seeds, RNG dumps, native save paths, privileged fingerprints and internal secrets belong only in the private manifest. Benchmark agents must not receive a deterministic seed alias that can be reversed through a public manifest.

Use separate IDs for the experimental slot `(configuration, seed, agent, replicate)` and each execution attempt. Preserve infrastructure retries as children of that slot so they cannot inflate the number of independent samples.

### 8.4 Terminal outcome enum

Exactly one terminal record per execution attempt:

`WIN`, `GAME_LOSS`, `AGENT_PROTOCOL_FAILURE`, `AGENT_ABORT`, `BUDGET_EXHAUSTED`, `INFRASTRUCTURE_FAILURE`, `OPERATOR_ABORT`, or `INVALID_EVALUATION`.

Attach a structured reason, first relevant event, last verified observation, usage totals, elapsed time, interventions, and validity flags. A leak, corrupted starting profile, unauthorized assistance or replay divergence invalidates the evaluation rather than becoming a strategic loss.

## 9. Expert review workbench

This is a first-class product, not a final visualization task.

### 9.1 Required screens

**Run browser:** filter by agent, configuration, batch, review status and outcome. In blinded mode, hide outcome-related fields and model identity. Selecting “failed runs only” is itself outcome exposure and must be recorded.

**Chronological decision viewer:** show the exact agent-visible observation, visible effects/counters, ordered cards, costs, available operations, chosen action, and observed transition. Support previous/next step and jumps to shops, blinds, buys/sells and consumable use. Use text/card representations without requiring proprietary art assets. Agent requests and optional notes should be inspectable but hidden until the reviewer requests them.

**Annotation editor:** annotate one decision or a closed interval of decision indices. Capture alternatives, concern, horizon conflict, confidence and evidence. Edits append revisions rather than replacing history.

**Branch comparison:** show the common prefix, intervention point, changed actions, resource trajectories and both outcomes, with prominent human-assistance and hindsight labels.

**Human controller:** submit the same public action contract while viewing the same public state and history. Do not expose extra engine information. A native-GUI human condition may be added later but must be separately labeled if its information/interface differs.

### 9.2 Two-stage review and exposure

Default to prospective review: fetch only the observation/history through decision `t`, initially withholding the model's action as well. The reviewer may record their preferred action or acceptable alternatives, reveal the model's action, record a disagreement, and then reveal the transition. Once future events are viewed, do not describe subsequent annotations at earlier points as prospective.

Prevent leakage server-side. Do not preload future observations, terminal results, total run length, thumbnails, outcome-coded filenames, timeline endpoints or future search snippets into the browser. Use an opaque advancing cursor rather than a progress bar whose length reveals when the run ends. Record the furthest revealed event and any run-list outcome exposure.

A reviewer who remembers the seed/future must declare it. UI blinding does not erase prior knowledge. Finish first-attempt human comparison runs before reviewing those seeds' model trajectories; later rescues are not fresh attempts.

### 9.3 Annotation schema

Required fields:

```text
annotation_id, episode_id, reviewer_id, revision, created_at
start_decision, end_decision
review_mode: prospective | retrospective | mixed
exposure: max_event_seen, outcome_seen, model_identity_seen, prior_seed_exposure
judgment: acceptable | concern | likely_error | unclear
categories: zero or more taxonomy labels
horizons_in_tension: immediate | near_term | long_term (zero or more)
mechanism_summary: concise explanation of the concern
alternative_actions: zero or more structured actions or clearly labeled prose plans
confidence: low | medium | high
evidence_event_ids: public trace references
intervention_refs: zero or more branch IDs
```

Starting taxonomy: rules/scoring error; observation/interface failure; poor immediate risk assessment; premature investment; unnecessary short-term spending; economy/search mismanagement; insufficient development; premature commitment; failure to pivot; failure to convert terminal resources; ordering/targeting error; memory/plan inconsistency; other; unclear. Allow multiple labels and acceptable actions. These are human hypotheses, not automatic ground-truth labels.

Keep adapter/interface errors separate from agent mistakes. Do not force a single “losing move.” A sequence of apparently modest choices may be the right annotation unit. Do not grade fluent explanations, confidence claims, money held or scaling counters as success.

## 10. Checkpoints, interventions and fidelity

### 10.1 What a checkpoint must preserve

At a verified decision boundary, store native game state, RNG continuation where applicable, profile dependencies, relevant pending-effect state, private identity mappings, public observation, the public history prefix, agent memory, deterministic context-assembly state and budget counters. No original future response or hidden future outcome belongs in the resumed agent context.

Create a checkpoint before each strategic decision where verified native saving supports it. Otherwise restore a certified earlier checkpoint and replay the exact committed prefix. If neither method is reliable at a decision, the UI MUST disable branching there with an explicit reason. Logging and review can still work; do not claim universal checkpoint support.

Store a fidelity certificate containing environment hashes, tested phases, trace suffix lengths, comparison criteria and failures. Certification is empirical for the pinned environment, not proof that every possible game state is supported.

### 10.2 Fidelity test

For representative checkpoints in each supported phase, execute a recorded action suffix, restore the checkpoint in a fresh compatible game process, replay the same actions and compare every resulting public state and terminal result. Compare a canonical private gameplay fingerprint too if available, excluding irrelevant timestamps/pointers. Test restoration repeatedly, including ordered objects, dynamic counters, multi-choice packs and stochastic effects.

Test seed-from-start replay separately from checkpoint restoration. The same initial seed does not license an assumption that different action sequences consume randomness identically. Capture the first divergence with both traces. Replay divergence disables affected intervention scoring until repaired.

### 10.3 Intervention types

| Type | Procedure | Interpretation boundary |
|---|---|---|
| `agent_continue` | Resume the unassisted agent from the checkpoint. | Diagnostic continuation baseline, not another independent starting seed. |
| `single_action_override` | Human substitutes one game action; same agent resumes. | Tests a local correction with that continuation policy. |
| `short_human_sequence` | Human controls a declared number of strategic actions, then returns control. | Tests coordinated local correction; log actual length. |
| `human_takeover` | Human controls the remaining run. | Demonstrates an attempted human continuation, not optimality. |

Every branch MUST have a new episode ID and immutable parent checkpoint/event reference. Never mutate the original trace. Branches retain the original history only up to the intervention point and append the actual substituted actions.

Default model memory on an override is the pre-decision memory, not the original action's rationale or memory update. The resumed model sees what actually happened through the normal public history. Human-written strategy notes are additional assistance and require a separate intervention label. Record how remaining budgets and provider randomness were handled; default to the same remaining budget as at the checkpoint.

A successful rescue shows a successful alternative continuation in that trial. It does not establish the expected-value-optimal move. A failed rescue does not prove an unwinnable state. Human takeovers with prior future exposure must be labeled hindsight-assisted.

Identical RNG restoration is reproducibility, not independent resampling. V0 MUST NOT publish expected regret, action value estimates, or causal percentages from one saved future. Hidden-state resampling conditioned on public information is deferred.

## 11. Experimental protocol and metrics

### 11.1 Seed and configuration discipline

Generate seed panels before viewing performance; validate native seed formatting and deduplicate canonical seeds. Partition development and held-out seeds before tuning. Store private seeds separately from published aliases and commit to the manifest cryptographically. A held-out run exposed during debugging becomes development data and needs a replacement selected under the original rule, not by difficulty.

Use the same seed/replicate slots for every agent under a configuration. Reset memory and provider sessions between episodes. Record a separate model sampling seed where supported; never confuse it with the game seed. Deterministic identical reruns are not additional independent environments.

Freeze game/profile, observation renderer, prompt, rules, tools, budgets and model settings for a batch. No cross-run learning, seed-specific advice, adaptation to held-out results or manual rescue in autonomous scoring. Future fixtures derived from one parent run, seed or matched scenario family must stay within the same development/test split.

First-attempt human results require declared seed-naivety and the same configuration/information policy. Do not assert a near-perfect human ceiling from the owner's experience alone. Record native-GUI versus structured-interface differences instead of calling them identical conditions.

### 11.2 Slot accounting and primary outcomes

For each agent/configuration, report the number of planned seed/replicate slots, executed attempts, valid outcomes, wins, game losses, protocol failures, agent aborts, budget exhaustion, infrastructure failures, operator aborts and invalid evaluations.

A valid autonomous outcome is one of `WIN`, `GAME_LOSS`, `AGENT_PROTOCOL_FAILURE`, `AGENT_ABORT`, or `BUDGET_EXHAUSTED` with no evaluation-invalidating flag. Protocol failure, refusal/abort and spent budgets are not excluded just because the game never reached a native loss.

Let `N` be planned slots, `V` valid resolved slots, `W` wins among them, and `U = N - V` unresolved/invalid slots. Report:

- **Observed autonomous success:** `W / V`, explicitly labeled with valid coverage `V / N` when incomplete.
- **Coverage/accountability:** all terminal counts and `V / N`; never report a successes-only denominator.
- **Missing-outcome sensitivity:** `[W / N, (W + U) / N]`, labeled as bounds from unknown slot outcomes, not a confidence interval.

When `V=0`, the observed rate is null, not zero. Do not use incomplete batches to announce a definitive ranking. Do not pool different deck/stake configurations into one score without a separately declared aggregation design.

Infrastructure retry policy: at most one replacement attempt per failed slot in the pilot, only for infrastructure failures, under the same frozen configuration and seed. Retain all attempts, choose the first valid completion, never the best result, and report all incurred resource use. Do not rerun ordinary losses, protocol failures or budget failures into a more favorable score. Data-leak invalidations require repairing and re-versioning the affected batch, not quietly replacing one embarrassing run.

### 11.3 Uncertainty and paired comparison

Implement seed-cluster bootstrap intervals: resample seed IDs with replacement, retaining all their agent/replicate observations, and recompute the declared statistic. Use the same sampled seeds for paired agent differences; use a fixed analysis RNG seed and 10,000 bootstrap resamples. Do not treat decisions or intervention branches as independent seeds.

Primary paired comparisons require matching valid slots and must disclose excluded/missing pairs. Show missing-outcome sensitivity beside them. Pilot intervals are exploratory; do not claim the 20-seed panel is a definitive population estimate or invent a power analysis. If only one effective seed or no valid variation exists, flag uncertainty as inadequately estimated.

### 11.4 Secondary outcomes and diagnostics

Report native progress descriptors (displayed ante/blind, actual boss victories and blind defeats), action validity, resource use, latency and failure classes. Do not infer success from “24 rounds” or reward skipping/avoiding blinds as if it were fighting them. Ante-changing effects require native terminal semantics.

Report input/output/cached/reasoning tokens separately when available, total provider calls, attempted and committed actions, game-processing versus provider time, helper use and estimated dollars with pricing provenance. Distinguish costs of all attempted evaluations from costs of valid completions.

Expert review reports MUST give their review-selection rule and denominator: reviewed runs, decisions/intervals and fraction of eligible data. Include a random sample of wins as well as failures when estimating error prevalence. A hand-picked failure collection is qualitative evidence, not a population error rate.

Useful v0 diagnostic outputs are distributions of annotated error categories and horizon conflicts, examples supported by public event IDs, intervention outcomes by assistance type, and the length/location of successful human sequences. Do not call these causal shares of failure, objective regret, or a universal horizon-intelligence score.

### 11.5 Baselines

Implement a seeded random-legal baseline as a plumbing check and a transparent configurable heuristic baseline as a stronger development comparator. Both use the same public interface. The random policy must not select hidden targets or call evaluator tools.

For the heuristic, document hand-selection logic, discard policy, buying/selling thresholds, economy rules, skipping and consumable behavior. Make priorities deterministic with explicit tie-breaking. If it uses a rank-only approximation or omits complex interactions, name that limitation; do not label it an exact greedy scorer or a strong human-equivalent baseline.

Add a replay agent for fidelity tests and a human controller for domain comparison. Stronger heuristics and mechanics-assisted conditions can follow the first diagnostic pilot. Weak baseline performance alone does not demonstrate sophisticated horizon reasoning.

## 12. Deliverables, interfaces and example configuration

### 12.1 Repository structure

```text
balatro-horizons/
  README.md
  AGENTS.md
  pyproject.toml
  <dependency lockfile>
  .env.example
  configs/{smoke,pilot}.yaml
  schemas/{observation,action,event,annotation,manifest,summary}.schema.json
  src/balatro_horizons/
    cli.py
    engine/ observations/ actions/ agents/ runner/
    storage/ review/ evaluation/
  web/
  tests/{unit,contract,integration,replay,e2e}/
  fixtures/offline/
  docs/{capability_audit,visibility_policy,protocol,setup,limitations}.md
  docs/decisions/
  reports/verification/
```

Do not check private run data, seeds, native saves, credentials or game assets into version control. Versioned offline fixtures must identify whether they are synthetic tests or legitimately shareable recorded data. Never label synthetic fixtures as real benchmark evidence.

### 12.2 CLI target

These commands are interfaces the agent must implement, not claims that software already exists:

```bash
bh doctor --config configs/smoke.yaml
bh run --config configs/smoke.yaml --agent random_legal --offline
bh run --config configs/pilot.yaml --agent model_a --seed-file private/dev.json --slot 0 --dry-run
bh batch plan --config configs/pilot.yaml --seed-file private/dev.json
bh batch run --plan private/plan.json --allow-paid --max-episode-cost-usd 10 --max-batch-cost-usd 200
bh review --data-dir data --host 127.0.0.1
bh replay verify --episode-id EPISODE_ID
bh branch --episode-id EPISODE_ID --decision 42 --mode single_action_override
bh report --batch-id BATCH_ID --output reports/BATCH_ID
bh export --batch-id BATCH_ID --public --output exports/BATCH_ID
```

Dollar values above illustrate cap arguments, not spending authorization or expected cost. Paid mode must remain disabled until explicitly enabled by the operator. `--dry-run` validates and renders plans without model calls or game mutation. `--offline` uses the labeled fake/replay adapter, not an unannounced replacement for the game.

CLI help and errors must distinguish dependency failure, schema error, invalid action and infrastructure failure. `doctor` should return structured JSON as an option and a nonzero exit code for missing live requirements.

### 12.3 Pilot configuration shape

```yaml
benchmark:
  name: balatro-horizons
  protocol_version: "0.1"
  track: structured-core
  objective: native_run_win
  target_ante: 8

environment:
  adapter: balatrobot
  deck: YELLOW
  stake: GOLD
  unlock_profile: dedicated_fully_unlocked
  resolved_manifest: private/environment.lock.json
  require_live_certification: true

sampling:
  split: development
  seed_manifest: private/dev.json
  seed_count: 20
  model_replicates_per_seed: 2
  max_infrastructure_replacements: 1

agents:
  - id: model_a
    provider: ${PROVIDER_A}
    model: ${MODEL_A}
    credentials_env: PROVIDER_A_API_KEY
  - id: model_b
    provider: ${PROVIDER_B}
    model: ${MODEL_B}
    credentials_env: PROVIDER_B_API_KEY

harness:
  prompt: configs/prompts/harness.txt
  rules_manifest: private/rules.lock.json
  recent_public_events: 20
  memory_max_characters: 4096
  optional_decision_notes: true
  max_helper_calls_per_decision: 8
  max_consecutive_invalid_actions: 3
  tools: [rules.lookup, history.read, arithmetic]

budgets:
  max_game_actions: 1500
  max_provider_calls: 2000
  max_input_tokens_per_call: 32768
  max_output_tokens_per_call: 8192
  paid_calls_enabled: false
  max_episode_cost_usd: null
  max_batch_cost_usd: null
  pricing_manifest: null

execution:
  workers: 1
  checkpoint_policy: every_verified_decision_boundary
  raw_engine_logs: private
  export_policy: sanitized_public
```

Unresolved environment variables, missing locked manifests or incompatible context limits are configuration errors before a paid batch starts. Add provider-specific supported generation settings to the resolved manifest, not silently to a generic default. Supplying a mock configuration must not bypass live-certification requirements for a purported real run.

### 12.4 Required reports

Generate a self-contained local HTML report plus machine-readable JSON and CSV: batch coverage and outcome counts; per-agent success and uncertainty; paired comparisons when eligible; progress/failure diagnostics; cost/usage provenance; review coverage and annotations; branch outcomes; and known validity limitations. Every chart/table must link to the underlying immutable run IDs. Reports from offline fixtures require a prominent `SYNTHETIC / TEST DATA` label.

Public exports must be regenerated from the public schema, not assembled by copying the run directory and hoping private files were omitted. Include protocol/configuration hashes, public traces, eligible annotations and aggregate accounting. Exclude private checkpoints/seeds, hidden state, credentials, reviewer identities unless opted in, and privileged paths. Record export policy and version.

## 13. Milestones and exit criteria

Implement in this order. A milestone requires runnable behavior and tests, not just folders and interfaces.

| Milestone | Required result | Exit evidence |
|---|---|---|
| **M0 — Audit and lock** | Inspect host/repository; resolve dependencies; audit action coverage, visibility, profile and checkpoint support. | Capability/visibility documents, resolved or explicitly blocked environment lock, architectural decisions, `doctor` output. |
| **M1 — Offline vertical slice** | Fake/replay adapter, validated observation/action schemas, runner, event journal, terminal accounting and a simple chronological viewer. | One deterministic offline episode is recorded, reopened and annotated; invalid action and failure tests pass; no keys/network needed. |
| **M2 — Native execution and fidelity** | Full native action adapter, information filter, profile isolation, seeded run capture, replay and phase-specific checkpoint certification. | Real-game smoke trace and test artifacts; no silent action gaps; win/loss detection tests; verified restore evidence or explicit blocked phases. |
| **M3 — Review and intervention** | Full prospective viewer, exposure tracking, annotation revisions, human controller, immutable branches. | E2E review with server-side future withholding; successful fork mechanics and provenance; original trace unchanged. |
| **M4 — Pilot and reporting** | Shared model harness, one real provider adapter, two model configurations, baselines, batch planner, budgets, reports, export scanner. | Offline batch passes all accounting tests; when dependencies and spending are authorized, live pilot results are generated with complete provenance and limitations. |

The first demo should reach M1 before building sophisticated statistics or a polished dashboard. Live-science readiness requires M2–M4 validation; a pretty offline demo is not a validated benchmark.

## 14. Acceptance tests

Implement automated tests with stable IDs and include their results in `reports/verification/`. “Live” tests require the pinned game. Skipped tests must appear as skipped/blocked in the release summary.

| ID | Required behavior and pass condition |
|---|---|
| AT-01 Public allowlist | Inject seed/RNG/hidden keys into every relevant raw structure; none appear in observations, model requests, errors, filenames or public exports. |
| AT-02 Visibility noninterference | Two private states differing only in information hidden under the same public history yield identical public projections and public legal constraints. Test outputs/metadata, not merely a list of forbidden key names. |
| AT-03 Hidden identity | Face-down cards and concealed shuffled Jokers cannot be tracked through internal IDs or handle persistence when a player cannot track them. Publicly knowable history remains available. |
| AT-04 Dynamic state | Public counters, effects, prices, capacities, hand levels and ordering update correctly after native actions; no static-text substitution. Include negative money and large numeric round-trips. |
| AT-05 Action coverage | Every required native strategic interaction is represented; live tests include blind skipping, relevant boss reroll, non-shop consumable use and multi-choice packs. A missing action blocks the affected configuration. |
| AT-06 No silent strategy | Harness never selects a blind, exits a shop, chooses a pack item/target or substitutes a move without an authorized actor event. Automated confirmations have audit evidence. |
| AT-07 Invalid action isolation | Malformed, duplicate-target, invalid-phase, stale-ID and unaffordable actions return safe errors, cause no game mutation, and consume the documented attempt budget. |
| AT-08 Ordering | Reordering hand/Jokers is preserved through execution and restore, and target IDs still refer to the intended publicly identifiable objects. No implicit sorting before play. |
| AT-09 Exactly-once boundary | Inject a timeout after native application but before acknowledgment; the action is not applied twice. Unresolvable status terminates as infrastructure failure. |
| AT-10 Checkpoint fidelity | Fresh-process restore reproduces a recorded suffix at each supported phase, including public states, dynamic counters, order and terminal result. Emit a first-divergence artifact on failure. |
| AT-11 Seed replay | Same environment/profile/seed plus the same recorded committed actions reproduces tested traces; private seed remains absent from the agent surface. |
| AT-12 Terminal correctness | Native Ante-8 win is distinguished from game loss and intermediate progress; skips and ante-changing effects do not create false wins. Exactly one terminal record is produced. |
| AT-13 Journal recovery | Crash during intent/result persistence; recovery preserves acknowledged actions, flags ambiguous ones, retains all attempts and rebuilds the same SQLite index. |
| AT-14 Budget safety | Corrections, helper calls and retries are counted; caps stop further requests before unauthorized spend; unknown pricing cannot silently enable paid execution. |
| AT-15 Provider parity | Equivalent public context produces the same canonical prompt/tool content through different provider adapters; unsupported settings fail validation. Secrets never enter traces. |
| AT-16 Temporal review isolation | A browser/API client at event `t` cannot obtain future state, terminal outcome, total-run length or future thumbnails via ordinary review routes; reveal history is recorded. |
| AT-17 Annotation provenance | Decision-range annotation and revision preserve reviewer exposure, confidence, evidence and alternatives; retrospective edits cannot masquerade as original prospective labels. |
| AT-18 Immutable branching | Override branches share exactly the authorized prefix, omit original future responses/memory, retain ancestry and assistance labels, and do not change parent hashes or autonomous scores. |
| AT-19 Accounting | Synthetic mixed-status batch produces exact expected planned/valid/win/failure counts, first-valid retry selection, missing-outcome bounds and all-attempt costs. Test zero-valid and partially completed batches. |
| AT-20 Statistical unit | Bootstrap resamples seeds with their replicates; pairing is preserved; adding more decision events or branches does not inflate the sample count. |
| AT-21 Export isolation | Automated scan plus schema validation finds no seeds, hidden state, raw saves, internal paths or credentials in public exports. Adversarial strings in notes are escaped in the UI. |
| AT-22 Offline honesty | A clean environment without game/API keys can run offline tests and review demo fixtures; all resulting artifacts are visibly synthetic and no live test is reported as passing. |
| AT-23 Profile/environment isolation | Runs cannot change the owner's personal profile, unlock state does not drift between slots, and environment mismatches prevent certified replay. |
| AT-24 Gate enforcement | Attempts to run an uncertified real configuration, expose a debug RPC through the agent gateway, or mix intervention outcomes into autonomous reports are rejected. |

Include property-based tests where they help validate visibility, handle remapping, permutation validity and accounting invariants. Golden native traces must identify their game/mod versions and collection method. Do not replace native fidelity checks with a mock that simply repeats the expected output.

## 15. Definition of done and delivery report

V0 is complete when a configured and authorized host can produce a real autonomous run with every intended native strategic action available; preserve the exact public decision history; review it prospectively; annotate intervals; branch at certified checkpoints without corrupting the original; and generate an honest batch report with failure, budget and validity accounting.

All required tests must have outcomes. Unavailable host dependencies may leave the implementation partially complete but must remain explicit blockers. “Complete” must not mean “the files exist.”

The agent's final delivery MUST state: implemented milestones; commands actually executed; pass/fail/skip counts; locations of live versus synthetic evidence; environment and dependency pins; unresolved action/visibility/fidelity gaps; paid calls and cost if any; and exact owner setup steps that remain. Include no fabricated benchmark scores and no claims about model reasoning beyond observed evidence.

### 15.1 Priorities when scope is constrained

Prioritize information integrity and action completeness, then a runnable recorded episode, then review/annotation, then certified branches, then batch statistics and appearance. Continue independent offline work when external dependencies block native validation. Do not consume the whole project building charts while the agent cannot make a legal complete run.

### 15.2 Deferred research extensions

After reviewing real failures, consider matched checkpoint families, mechanics-assisted ablations, stronger scripted/search baselines, additional deck/stake configurations, shared-harness versus optimized-system tracks, properly conditioned counterfactual sampling, and explicitly modified-rule transfer tasks. Each needs a separate protocol version or track. None is required to deliver the first full-run benchmark workbench.

## 16. Evidence ledger and references

Only the upstream facts in Section 3 and the existing-name note are externally sourced here. Architecture, defaults, schemas, metrics, test requirements and milestone choices are proposals for this project. Public documentation is not a substitute for live compatibility/fidelity evidence.

**[S1] Existing BalatroBench and published methodology.** BalatroBench, “About.” Checked 2026-09-14. Supports the existing-project/name note and its published round/tool/resource metrics.  
`https://balatrobench.com/about.html`

**[S2] BalatroBot project.** Maintainer repository/README. Checked 2026-09-14. Supports the JSON-RPC mod starting point.  
`https://github.com/coder/balatrobot`

**[S3] BalatroBot API reference.** Maintainer documentation. Checked 2026-09-14. Supports the documented action/state/save/load surface and presence of the seed in raw state; does not establish exhaustive native parity or save fidelity.  
`https://coder.github.io/balatrobot/latest/api/`

**[S4] BalatroBot installation reference.** Maintainer documentation. Checked 2026-09-14. Supports the prerequisite tooling; the implementer must resolve compatible versions and local platform setup.  
`https://coder.github.io/balatrobot/latest/installation/`

**[S5] BalatroLLM game-loop source.** `src/balatrollm/bot.py`, floating `main` inspected on 2026-09-14. The observed `BLIND_SELECT` branch selects automatically. Pin the actual source revision if reusing it.  
`https://raw.githubusercontent.com/coder/balatrollm/main/src/balatrollm/bot.py`

---

**First build target:** a trustworthy full-run trace that the owner can inspect and annotate—not a leaderboard and not a claim that the game is solved.
