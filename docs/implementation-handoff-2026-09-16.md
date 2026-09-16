# Balatro Horizons — implementation handoff

**Owner:** Cesar E\
**Audience:** implementation agent\
**Prepared:** 2026-09-16\
**Review baseline:** `f85b3483da5c59a930307b01a3f2dc244e12439c`\
**Repository:** <https://github.com/cesaregarza/balatro-horizons>

## 0. Assignment and evidence status

Implement **Part A: the campaign-budget correctness fix** first, including the remaining defects in the reviewed candidate. Keep it as an independently reviewable change. **Part B is the prioritized follow-up backlog, not permission to bundle protocol changes into the budget patch.**

Inspect the actual checkout, current commit, working-tree changes, and repository instructions before editing. Do not assume the candidate patch is already installed, or overwrite unrelated changes. If the checkout differs from the review baseline, verify the relevant behavior rather than blindly applying old hunks. This document is sufficient to implement the target behavior without the earlier conversation.

### What has and has not been verified

- The original defect was reproduced by Claude with the real batch/report path, a synthetic game, and mocked provider transport: an unfunded slot was counted as a valid non-win despite making zero provider calls.
- Claude supplied a candidate patch and reported **172 tests passed, 9 native gates skipped, and `ruff check` clean**. These are reported results for that candidate, not a claim that the final implementation below has passed.
- A supplied isolated reproducer copies the candidate budget/scheduler logic and uses lightweight execution/storage substitutes. It demonstrates three remaining defects: a rerun bypasses an interrupted slot, floating-point admission predicates disagree, and campaign preflight hides invalid episode configuration. It is **not** a full-repository or native validation.
- The candidate's report/export test only calls `report_batch()`. Its export-coverage claim is not established by that test.
- No final patch, live-provider validation, native recertification, deployment, or improved gameplay result is established by this handoff.

Optional companion evidence: [isolated reproducer](review_candidate_budget.py) and [recorded results](review_candidate_budget_results.json). These files explain the failures; port the cases into tests against the production modules. The important inputs and expected results are also included below.

### Execution boundaries

Use synthetic games and mocked transports for implementation tests. Do not make paid calls, launch or alter the Windows game/runtime, change spending authorization, deploy the workbench, or regenerate native certificates without explicit task-scoped authorization from Cesar E. Do not weaken gates, edit certificate hashes to make them pass, rewrite old episode evidence, or alter the precommitted seed panel.

---

# Part A — campaign-budget correctness

## A1. Problem and target result

The original implementation merges episode and campaign limits into `COST_CAP_REACHED`. The runner records `BUDGET_EXHAUSTED`, which the batch reporter treats as a valid non-win. Scheduling then continues through later slots. In native execution, a slot can construct a game before discovering that its first provider request cannot be funded.

**Core distinction:** the agent's predeclared episode resource allowance is an evaluation condition; the operator's shared campaign funding ceiling is an external stopping condition.

For a two-slot synthetic fixture where only the first slot completes and the next cannot fund its first request, require:

```text
planned=2  attempts=1  valid=1  wins=1  unresolved=1
win_rate=1.0                       # conditional on valid outcomes
coverage=0.5
missing_outcome_bounds=[0.5, 1.0]
scheduling_stop.reason=CAMPAIGN_COST_CAP
scheduling_stop.stage=preflight
```

Do not describe the conditional `1/1` as the true performance of the planned batch. One planned outcome remains unknown. More generally, funding can interrupt longer or costlier trajectories; excluding those attempts does not establish that missingness is random.

## A2. Required invariants

| Situation | Episode result | Resolves a slot? | Scheduler behavior |
|---|---|---:|---|
| Next paid slot cannot fund its first reservation | No episode created | No | Record durable funding stop; construct no game; stop scheduling |
| Only the episode cost allowance refuses a request | `BUDGET_EXHAUSTED` / `EPISODE_COST_CAP` | Yes, under the existing resource-bounded protocol | May continue to later slots |
| Only campaign funding refuses a request inside an episode | `CAMPAIGN_INTERRUPTED` / `CAMPAIGN_COST_CAP` | No | Preserve the partial trajectory and costs; stop scheduling |
| Both episode and campaign limits independently refuse the same request | `BUDGET_EXHAUSTED` / `EPISODE_AND_CAMPAIGN_COST_CAP` | Yes | Retain the episode-budget result and stop scheduling |
| Paid configuration lacks required caps or episode cap cannot fund one reservation | Configuration/authorization error | No | Reject before episode creation or game construction |
| Provider usage is unknown after a transport failure | Existing transport-failure semantics | No new budget interpretation | Retain the full reservation; count it in subsequent affordability checks |
| Existing action/call/helper allowance is exhausted | Existing resource-budget outcome | Unchanged | Do not invent campaign exhaustion |

For simultaneous limits, episode allowance takes precedence **only when it independently refuses the request**. Record that the campaign limit also binds so the scheduler stops. Classification must not depend on the ordering of two conditionals.

`CAMPAIGN_INTERRUPTED` must remain outside `evaluation.batches.VALID`. Keep interrupted attempts in attempt counts, outcome categories, and all-attempt cost accounting. Do **not** mark such attempts `evaluation_eligible=False` as a shortcut: the current reporter filters ineligible records before accumulating costs.

## A3. Shared reservation and affordability logic

### Keep reservation sizing unchanged

Extract one reservation calculation and use it in the runner, batch preflight, and the existing smoke preflight:

```python
required_usd = (
    limits.max_input_tokens_per_call * model.maximum_input_usd_per_million
    + limits.max_output_tokens_per_call * model.output_usd_per_million
) / 1_000_000
```

Use the **next slot's actual provider model**. Do not use the first model's prices, a batch average, or a campaign-wide constant. Scripted/free agents must not acquire a fictitious paid reservation.

Keep existing worst-case rates, output ceilings, retry charging, and unknown-usage conservatism. Tokenizer-aware sizing, pricing changes, and fixed-point money migration are separate work.

### Share the admission predicate too

The candidate preflight uses `amount <= cap - total`, while reservation uses `total + amount <= cap`. They can disagree in floating-point arithmetic.

Required regression input:

```text
cap_usd=0.031344
committed_usd=0.01496
required_usd=0.016384

Candidate headroom: 0.016383999999999996
Candidate can_fund: False
Candidate reserve:  accepts
```

For this patch, use the **same expression, numeric representation, and committed-cost calculation** in both places. Preserve the authoritative reservation comparison, e.g. `total + amount <= cap`. Headroom is diagnostic; do not rearrange the admission calculation through subtraction. Do not introduce an epsilon that allows overspending.

Read settled costs plus retained unsettled reservations from the same ledger semantics. A preflight may lock briefly to read a consistent snapshot, but it does not reserve funds. The locked `reserve()` immediately before every actual request, including retries, remains authoritative. Use one snapshot for a stop's affordability decision and explanatory totals where practical.

Validate missing/invalid caps as configuration errors; do not report them as exhausted campaign funding. Preserve recorded overages and negative headroom if they occur rather than making the report imply funds remain.

## A4. Validate configuration before preflight

Extract or reuse side-effect-free model/budget validation. Invoke it before the selected paid slot's funding preflight, and reuse it on the single-run path. Do not construct an extra provider client just to validate settings.

Reject `max_episode_cost_usd < required_usd` with `EPISODE_CAP_BELOW_RESERVATION` before any episode or game exists, even when campaign funding is also insufficient.

Required regression input:

```text
episode_cap=0.010
campaign_cap=0.005
required_reservation=0.016384

Expected: configuration error EPISODE_CAP_BELOW_RESERVATION
Not:      scheduling_stop.reason=CAMPAIGN_COST_CAP
```

Preserve existing authorization and credential checks. Configuration errors must not become valid agent outcomes or misleading funding-stop records. Do not require paid-model validation for an unrelated scripted baseline.

## A5. Make funding stops survive reruns and crashes

### Required policy for this patch

A campaign funding stop is **sticky for the existing fixed-configuration batch**. An ordinary rerun is not authorization to bypass it, switch to cheaper slots, replace an interrupted episode, or increase funding. Preserve original planned order and slot matching.

Before scheduling new work:

1. Validate the plan/configuration and evidence-kind constraints.
2. Reconcile durable batch-stop information with original batch-attempt terminal evidence.
3. If a prior campaign-stop reason exists, return the stopped batch without creating another episode or game.
4. Otherwise, inspect the next schedulable slot, validate its configuration, and perform its model-specific funding preflight.

After execution, inspect the committed terminal result, not only an exception or a transient return value. Stop for both `CAMPAIGN_COST_CAP` and `EPISODE_AND_CAMPAIGN_COST_CAP`.

Only original batch attempts should govern this decision; a diagnostic assisted branch must not create a new scheduling policy for the autonomous batch.

### Candidate rerun defect to eliminate

An existing `CAMPAIGN_INTERRUPTED` attempt is not retryable under the current `while` condition. The candidate therefore skips that slot on rerun, and can execute a later cheap model despite the prior stop.

Isolated reproduction, with unchanged configuration:

```text
campaign cap:             $1.70
episode cap:              $2.00
pricey reservation:       $1.6384
cheap reservation:        $0.016384

First invocation:
  pricey -> 2 calls, cost $0.088, CAMPAIGN_INTERRUPTED
  cheap  -> not started

Candidate second invocation:
  pricey -> skipped because an interrupted attempt already exists
  cheap  -> 5 calls, WIN, cost $0.0022
  prior stop record remains unchanged
```

**Target:** the second invocation performs no new calls and constructs no game. Test with a fresh `RunService` instance so in-memory state cannot mask the bug.

### Durable stop representation

For this fixed-cap patch, an immutable first-stop record is sufficient; a generic event-sourcing rewrite is not required. If `stop.json` remains a mutable status view, give it durable backing and preserve the original stop. Do not overwrite the first stop merely because the user reruns the command.

Capture a schema version, batch ID, time, reason, stage (`preflight` or `episode`), slot ID, agent, and appropriate cost context. Include episode ID/outcome and a terminal reference for an in-episode stop. Do not include seeds, credentials, private engine state, or private paths.

Handle the crash window where an episode terminal is committed but the stop record is not. Recover the stop from authoritative journal evidence before scheduling anything else. A rebuildable index is not the source of truth. Distinguish a recovered-record timestamp from the original terminal timestamp. An unreadable stop record must not silently be treated as permission to continue.

Maintain safe write serialization using the repository's storage conventions. Verify first-stop preservation, not merely that some stop file exists.

## A6. Reporting, export, and compatibility

- Keep planned slots unchanged. Do not remove unfunded slots from the plan or denominator.
- Keep the first-valid-outcome selection policy and seed/replicate matching unchanged.
- Include all costs of externally interrupted eligible attempts, including retained unknown-usage reservations.
- Surface `scheduling_stop` consistently in batch report JSON and exported bundles. Inspect report/API/CLI consumers for any exhaustive outcome handling that needs the new enum.
- An actual `export_batch()` test must load the resulting `public.json` and inspect its report and episodes. Calling `report_batch()` alone is not export coverage. Exercise the existing privacy scan without weakening it.
- Old batches without a stop record must remain readable. Preserve old manifests and hash-chained journals; do not retroactively relabel generic historical `COST_CAP_REACHED` events without sufficient evidence and a separate versioned analysis policy.
- Keep `config_hash` behavior unchanged. Raising a batch cap continues to fail with `BATCH_CONFIGURATION_CHANGED`; no funding-amendment or resume mechanism belongs in this patch.

Describe a preflight as **non-binding**, not “non-locking”: the candidate `headroom()` acquires a read lock but does not create a reservation.

## A7. Production integration points

| File or area | Required work |
|---|---|
| `src/balatro_horizons/agents/budget.py` | Shared reservation and admission calculations; distinct refusal reasons; consistent ledger snapshots; retain unknown usage |
| `src/balatro_horizons/runner.py` | Use shared reservation; classify campaign interruption separately; journal costs and terminal evidence |
| `src/balatro_horizons/service.py` | Side-effect-free validation before preflight; avoid unfunded game construction; enforce durable stops on initial run and restart |
| `src/balatro_horizons/contracts.py` | Add/retain `CAMPAIGN_INTERRUPTED`; check outcome consumers |
| `src/balatro_horizons/evaluation/batches.py` | Durable stop access/reconciliation as appropriate; preserve valid-outcome and complete attempt-cost semantics |
| `src/balatro_horizons/evaluation/reports.py` | Carry stop metadata into reports and actual exports |
| `scripts/smoke_openai.py` | Replace copied reservation formula with shared helper; preserve authorization behavior |
| `tests/test_campaign_budget.py` | Test real modules with mocked transport and synthetic game; add candidate-regression cases |
| README and budget-fix documentation | State exact semantics, verification scope, known limitations, and native-gate consequences |

Avoid unnecessary refactors outside these paths. Do not copy the isolated reproducer's simplified storage implementation into production.

## A8. Acceptance matrix

The numbers below are **frozen synthetic fixture values**, not current provider-price claims. The earlier candidate fixture used a $0.016384 reservation, a $0.00044 settled call, and five calls to finish `FakeGame`.

| ID | Scenario | Required assertions |
|---|---|---|
| T01 | Two slots, episode cap $1, campaign cap $0.0184 | One synthetic win; no second episode/game; exact A1 report; repeat invocation creates nothing and preserves the original stop |
| T02 | Three slots, episode cap $1, campaign cap $0.0192 | First wins; second commits two actions then becomes `CAMPAIGN_INTERRUPTED`; third never starts; attempts=2, valid=1, unresolved=2, total cost=$0.00308 |
| T03 | Episode-only refusal, episode cap $0.017, ample campaign | Both selected episodes may reach `BUDGET_EXHAUSTED` / `EPISODE_COST_CAP`; valid non-wins; no false campaign stop |
| T04 | Both caps $0.017 | One `BUDGET_EXHAUSTED` / `EPISODE_AND_CAMPAIGN_COST_CAP`; later slots unresolved; durable stop survives rerun |
| T05 | Transport timeouts, campaign cap $0.04, one transport attempt per episode | Existing one infrastructure replacement retained; two unknown-usage reservations total $0.032768; next slot not constructed; costs not refunded |
| T06 | Mixed models | Use the actual upcoming model's reservation; reject the $1.6384 request when only a cheap model would fit; do not skip ahead |
| T07 | Mid-episode stop followed by cheap model; fresh service rerun | Reproduce A5's inputs; second invocation makes no new provider requests and constructs no game |
| T08 | Terminal committed before stop write | With stop metadata absent, reconstruct from campaign-stop terminal and schedule nothing later; also cover rebuildable-index recovery where applicable |
| T09 | Exact affordability boundary | A3 values yield matching preflight/reservation acceptance; test exact fit and adjacent representable floats against unchanged ledger snapshots |
| T10 | Invalid episode cap and insufficient campaign simultaneously | A4 error occurs before any episode/game; test both batch and single-run validation, plus missing required caps |
| T11 | Real report and real export | Invoke both functions; inspect `report.json` and exported `public.json`; verify stop, unresolved counts, interrupted costs, and privacy-scan success |
| T12 | Frozen configuration and compatibility | Increased campaign cap still fails hash check; old batches without stop read correctly; unrelated action/call/helper limits retain their classification |

Use constructor spies to prove no game is created for unfunded slots. A spy on the shared execution path with `FakeGame` provides offline coverage; a patched `NativeGame` constructor that raises if reached can additionally check native dispatch without launching Windows.

Use the existing real `Store`, runner, and reporter for integration tests. Use fault injection around terminal/stop writes for restart tests. Do not claim a copied-logic reproducer or a renamed test establishes production integration coverage.

## A9. Validation and delivery

From the repository root, using its locked dependencies and supported environment:

```bash
uv sync --locked
uv run pytest -q tests/test_campaign_budget.py
uv run pytest -q
uv run ruff check src tests scripts
git diff --check
```

Inspect repository instructions before running commands. Keep live/native/paid actions disabled. If dependencies are unavailable, report the actual blocker; do not claim the suite passed. Run the relevant existing frontend checks if a consumer requires changes.

Where feasible, demonstrate that added regression tests fail against the reviewed candidate or original behavior and pass against the final change. Record the actual checked-out commit and resulting diff; do not reuse the candidate's earlier test totals as final evidence.

**Native gate:** budget, runner, service, and contract edits fall within the reviewed `implementation_fingerprint`. Offline tests do not reauthorize native runs. State that native certification must be refreshed through the existing authorized release procedure before native use. `verify_release.py --resume-certification` is the previously identified path; inspect its current requirements and do not invoke it without native-access authorization.

Deliver a focused diff/commit, exact tests run and results, corrected documentation, and remaining limitations. Explicitly say whether native/provider checks were performed. Do not deploy as an incidental final step.

---

# Part B — separate follow-up work packages

These preserve the broader harness review without expanding Part A. Implement independently after confirming the intended protocol change and any required authorization. Do not rewrite historical traces or claim gameplay improvement from request-construction tests alone.

## B1. Repair missing or ambiguous public information

**High priority; representation correctness.**

- Preserve rank and suit for shop/standard-pack playing-card offers through native extraction, `normalize()`, `PublicOffer`, projection, and final provider serialization. Inspect the Lua label override as well. Keep concealed information masked; do not repair labels by exposing hidden identity.
- Represent native-visible voucher/tag descriptions rather than only internal keys. Separate active blind effects, offered skip rewards and their activation conditions, and acquired pending tags. Carry available blind status rather than making the model infer defeated/current/upcoming solely from ordering.
- Add deterministic last-action deltas from observed public states/events. Preserve the pre-action labels for cards whose handles leave the current state. Treat a model-authored hand name or prediction as a claim, not an engine fact.

**Acceptance:** test the final serialized model request, not only normalization. Verify unchanged prices/mechanics, hidden-state noninterference, and that fighting a blind never describes its unearned skip reward as acquired. Version public-contract/presentation changes.

## B2. Correct provider continuations and failure feedback

**High priority; integration correctness and explicitly chosen comparison protocol.**

- Preserve the relevant provider-native response items across helper/tool calls within one decision. Keep a deliberate, versioned reset at the game-action boundary if explicit across-action memory remains the protocol. Do not reconstruct only a tool call while silently dropping required continuation blocks.
- Check the current official documentation for the exact selected model before implementing thinking, tool-choice, or encrypted-reasoning handling. Manual and adaptive thinking support must not be assumed identical. Absence of an explicit encrypted-content `include` alone is not proof that no such content was returned; inspect actual responses.
- Distinguish truncated/incomplete responses, no tool call, multiple calls, unavailable tools, and invalid argument schemas. Return corrective feedback matching the actual error instead of always warning about nested envelopes.
- Decide whether the comparison uses matched tool enforcement or provider-native features. OpenAI API-side allowed-tool restriction and Anthropic local rejection are not equivalent generation constraints merely because schemas match.
- Treat caching transport, read/write pricing, worst-case reservations, and settlement as one integration. Do not add Anthropic cache controls while leaving cache writes outside the reservation bound or settlement semantics.
- Handle retry metadata and unknown usage conservatively. Do not promise transport retries recover a response or avoid duplicate billing without provider-supported evidence.

**Acceptance:** thinking-bearing tool turns survive required round trips; malformed/truncated responses receive correct feedback; inactive-tool behavior is documented and tested; cache categories reconcile; opaque provider content does not bypass export/privacy boundaries. Any live probe needs separate authorization.

## B3. Freeze the complete agent protocol at episode start

Snapshot actual prompt bytes, interface/tool policy, knowledge identity, model settings, and memory/retention policy. Current named-interface prompt files are reread during context construction; delivered-context logs make drift detectable, not impossible.

Branches inherit the frozen agent protocol unless explicitly marked as protocol-change interventions. Test that editing prompt files after episode start changes neither later requests nor ordinary branches.

Separately design engine-fidelity versus agent-protocol fingerprints. Do not merely exclude files to make native gates cheaper: preserve dependencies that affect execution, observation, action legality, restoration, and evidence integrity. Part A must still honor the existing gate.

## B4. UI-equivalent hand preview, not a solver

A proposed `preview_hand` should accept the model's explicit current selection and expose **only the hand name and base chips/multiplier actually visible in the native UI under that condition**. It must not become a final-score oracle, hidden-card inspector, move ranker, or automatic search over selections.

Require native-UI comparison, state/RNG nonmutation, ordering, and concealment tests. A function being a pure read does not establish information parity. Version the interface change and validate its actual behavior; do not claim it fixes Joker-timing or final-score expectation errors.

## B5. Context, memory, and note experiments

These are hypotheses to evaluate, not proven causes of the audited losses.

- Consider an agent-authored within-decision scratchpad or revised retention policy so multiple reads can be synthesized before committing an action. Keep it bounded, journaled, and distinct from across-action memory.
- Separate context-byte bounds from provider-token limits and funding reservations. Do not lower a byte threshold on the basis of measured tokenizer counts.
- Move genuinely static presentation definitions into the stable prefix. Short handles must still be based only on public encounter semantics and must not let concealed or untrackable identities be followed.
- A default-counter exception marker is acceptable only if derived from the observed completeness predicate. Do not assume every face-up card has all counters. Test round-trip semantics for complete, partial, concealed, and extra/zero-valued counters.
- A compact fixed rules kernel, structured expectations, revised note wording, or persistent retrieved knowledge changes the protocol. Test changes separately. Skill nonuse is observed in audited traces; its cause is not established. The audited primary traces did not exhibit exchange eviction, so eviction is not an established explanation for their mistakes.
- Separate neutral rules availability from situational prompts that supply the recognition being measured. Reported expectations are useful prospective evidence, not direct access to complete internal beliefs. Missing notes are not proof of absent planning.

Measure action validity, repeated retrieval, budget/context failure reasons, note coverage/specificity, cost, and valid full-run outcomes under matched conditions. Do not infer benefit solely from smaller request bodies or more verbose notes.

## B6. Reporting and engineering follow-ups

- Flag degenerate seed-bootstrap intervals for uniform all-loss/all-win samples. Preserve the seed-dependence model; do not silently replace it with independent-replicate assumptions.
- Add public offline CI and a distinct native-release lane. Missing/stale native evidence may be an explicit skip in a fresh offline checkout; it must not silently satisfy a release gate.
- Refactor versioned interfaces and hard-coded runtime paths only in separate changes with compatibility tests. Preserve legacy interface identities rather than rewriting old manifests.
- A future funding-amendment mechanism should distinguish frozen evaluation protocol from operator authorization and define interrupted-slot treatment. It must not silently grant retries or select favorable continuations.
- Version research rubrics and avoid retroactive relabeling. The reviewed research intent distinguishes blind/ante/run horizons; game performance alone does not validate the proposed safety proxy.

---

# Source map

The following links identify the reviewed baseline, not a claim about the current repository head. The candidate patch and its reported tests were supplied in the review conversation; the optional local reproducer files are limited-scope supporting evidence.

- [Budget reservations](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/agents/budget.py), [runner](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/runner.py), and [service/scheduler](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/service.py): Part A control flow and accounting.
- [Batch summaries](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/evaluation/batches.py), [reports/exports](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/evaluation/reports.py), and [journal](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/storage/journal.py): denominators, cost aggregation, evidence, and recovery.
- [Native normalization](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/engine/native_state.py), [contracts](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/contracts.py), and [projection](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/observations/projection.py): public-information boundary.
- [Provider adapters](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/agents/providers.py), [protocol](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/agents/protocol.py), and [focused context](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/agents/focused.py): continuation, prompt loading, and retention.
- [Provenance fingerprint](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/src/balatro_horizons/engine/provenance.py): current certification scope.
- [September 15 transcript audit](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/docs/harness-efficiency-audit-2026-09-15.md) and [research reconciliation](https://github.com/cesaregarza/balatro-horizons/blob/f85b3483da5c59a930307b01a3f2dc244e12439c/docs/research/reconciliation-2026-09-14.md): diagnostic evidence, limitations, and research/protocol distinctions.

**Definition of done for this handoff:** Part A's production regression tests pass; spending and outcome semantics are correct across first run, rerun, and the terminal/stop crash boundary; reports and actual exports agree; evidence remains intact; native/provider authorization is not assumed; Part B has not been silently bundled into the fix.
