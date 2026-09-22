# Changelog

## 2026-09-22 — Budget continuations ported to the current packages (PR #25)

- Added `bh evidence plan/collect --continuation-only` for a bounded unpaid
  initial-blind fixture plus three fresh-process probes, without backend
  deployment. Immutable receipts preserve failures and explicitly exclude
  cost-stopped-root or later-phase certification.
- Integrated current main, including #23, without rebasing the published branch.
  Budget preparation and ledger reconciliation now live in `workbench/`; probe
  certification and its native identity live in `evidence/`.
- Preserved all-attempt spend, retained-reservation reconciliation, zero-spend
  child ordering, strict frozen-protocol identity, and probe/ordinary-certificate
  isolation. Native admission uses explicit session preflight and the worker's
  nonblocking reservation. Budget routes exist only with the workbench enabled.
- Replaced `scripts/continue_budget.py` with `bh continue-budget`. Its read-only
  plan consumes sanitized saved-versus-certified status, using the probe pointer
  for cost-stopped roots. Retired the unconsumed latest-model selector and folded
  the standalone budget guide into the dashboard/evidence layer guides.
- Ordinary replay failure classification remains unchanged; #42 tracks the
  ambiguity separately. Offline fixtures and CI do not establish the fresh,
  source-bound native continuation proof required before this PR leaves draft.

## 2026-09-22 — Explicit credentials and Windows connection (PR #23)

- Added `bh evidence plan --connection-only` and `bh native diagnose
  --connection --report PATH` for one owned calibration launch, fail-closed
  unregistered RPC, registration refresh, same-process RPC reconnection, and
  cleanup. Immutable receipts bind the source and environment without granting
  capability or replay certification; offline doubles cover failure cleanup.

- Integrated current main without rewriting the published PR history. Ported
  session isolation to `game/transport.py`, runtime status to the split API and
  run-library screen, and credential configuration to `bh credentials`.
- Native admission requires explicit owner-only session registration; every new
  bridge process excludes provider credentials and proxy environment variables.
  Refreshing the session starts no process and does not restart the backend.
- Frozen batches retain their original native/synthetic evidence kind after a
  failed session preflight. Explicit session expiry aborts replay verification
  without replacing a passing certificate; other transport failures remain
  conservatively failed replay evidence. Worker contention fails promptly.
- Folded `docs/runtime-connection.md` into the layer guides and removed the
  standalone credential script in favor of its tested package command.
- Offline checks do not supply the fresh source-bound native launch/RPC proof
  required before this PR leaves draft. No prior certificate is reused here.

## 2026-09-21 — Operational commands move into the package (issue #40)

- Moved the nine import-loaded scripts into `cli/` modules with `bh` homes;
  their tests now import package modules and retain their behavioral properties.
- Kept `scripts/check_offline.py` as the two-line CI entry point, preserving
  the Python and web lane commands. Native bootstrap remains unchanged.
- Moved worker status to `bh review status`, separate from environment doctor
  checks, and browser-native verification to `web/scripts/` with its existing
  web unit-test consumer. Retired the other Python script entry points.
- Parser registration preserves existing command behavior, including paid-call
  opt-in, caps, native diagnostic sequencing, and install/rollback safeguards.
  Installer runtime defaults now come from the shared environment definition.
- This packaging change supplies no new native or paid-provider certification.

## 2026-09-21 — Documentation consolidation (PR #39; issue #17)

- Consolidated the README, architecture, game-interface, harness, dashboard,
  and evidence guides; historical records now live here newest-first.
- Deleted the 19 retired loose docs (`cache-diagnostics`, `cache-fix`,
  `campaign-budget-fix`, `current-costs`, `dashboard-performance-and-modifiers`,
  `decision-summaries`, `defaults-refactor`, `dev-inspector`,
  `frozen-agent-protocol`, `harness-efficiency-audit`, `harness-reliability`,
  `harness-skills`, `implementation-handoff`, `input-reconciliation`,
  `live-decision-explorer`, `native-audit`, `native-mod-feasibility`,
  `public-information-v1.1`, and `release-2026-09-16`). PR #39 records the final
  per-package line totals against its reviewed base.
- Preserved the contract, Balatro guide, third-party notice, and research
  reconciliation/return handoff files byte-for-byte. The observer UX proposal
  remains parked and was not silently adopted or dropped.
- B1–B3 are implemented. B4/B5 and the unfinished B6 items remain in the
  implementation-handoff backlog below; the script collapse is recorded here.

## 2026-09-21 — Script collapse (PR #39; issue #17)

- `probe_prompt_cache.py` (introduced 2026-09-15, `f85b348`) asked whether a
  bounded API-only probe demonstrated reusable prompt input; recorded traces,
  not a one-shot live probe, remain the accepted evidence.
- `audit_cache_layout.py` (2026-09-15, `f85b348`) asked whether recorded public
  requests shared a stable cache prefix; the historical audit established the
  comparison method, now documented in the harness guide.
- `audit_transcript_efficiency.py` (2026-09-15, `f85b348`) asked where verified
  public transcripts spent tokens and bytes; the answer was to improve
  presentation and measure caching separately, not enlarge the prompt.
- `inspect_game.py` (2026-09-15, `f85b348`) asked which selected proprietary
  source lines supported native-interface diagnosis; source fingerprints and
  the typed game boundary now carry the durable result without extracted files.
- `verify_cost_evidence.py` (2026-09-16, `f66a533`) asked whether completed
  native public journals persisted costs and reconstructable receipts; the
  public cost and transaction contracts now own those invariants.
- `verify_gated_run.py` (2026-09-15, `f85b348`) asked whether ordinary certified
  startup could finish with calibration disabled and no provider spend; the
  recorded answer was yes for its pinned configuration, not a general native
  certification claim.
- `audit_run_notebook.py` (2026-09-18, `b51c0be`) asked whether persistent notes
  were actually delivered and exposed; the append-only journal and review
  service remain the authoritative record.
- `source_metrics.py` (2026-09-20, `21f7486`) asked for reproducible Python/Lua
  size and function metrics during the restructure; the bounded review was
  completed and the one-shot reporter has no runtime consumer.
- Moved `decision_summary.py` and `summarize_run.py` into the `bh summarize`
  package command, including descriptors in the installed wheel, and moved
  `native_patches.py` to `game/patches.py`.
- `bh summarize` follows the shared CLI convention: output failures return 1
  with `SUMMARY_OUTPUT_EXISTS` or `SUMMARY_OUTPUTS_MUST_DIFFER`, and success
  prints a JSON result instead of the script's `Recorded N actions in …` line.
- `inspect_divergence.py` moved to `bh evidence inspect` in PR #38; it answers
  which public boundary first diverged without printing private values.
- Four load-bearing operational exceptions remain separate:
  `configure_workbench_session.py`, `diagnose_native_startup.py`,
  `install_candidate.py`, and `verify_browser_native.mjs`.
- Nine retained operational-script tests still load their script boundary
  directly. Issue #40 owns their package moves, with property-preserving tests.

## 2026-09-21 — Native evidence pipeline (PR #38)

- Moved collection/certification responsibilities behind `EvaluatorSession`,
  preserved public/private evidence separation, and retained explicit reuse
  requirements. Native execution and paid-provider verification remain operator
  work, not documentation acceptance.

## 2026-09-21 — Harness money and loop consolidation (PR #36; issue #14)

- Merged reservations and durable batch scheduling into `harness/money.py`,
  with one `REFUSAL_OUTCOMES` table, explicit `Spending.retain`, and required
  ledgers for named episode-loop phases.
- Moved harness helpers under `harness/`, removed the replaced runner, agent
  package, and scheduling module, and documented reserve-before-send and
  campaign-versus-episode semantics in `docs/harness.md#money`.
- `episode_start` now sits inside the episode-loop `try` block, so a journal
  failure while recording it is classified and closed with the run instead of
  escaping before a terminal record.

## 2026-09-21 — Dashboard boundary (PR #37)

- The dashboard split keeps exploration, settings, batches, reports, and
  retrospective annotations available by default; staged review, branches,
  takeover, and verification remain workbench-only. Budget continuation belongs
  to PR #25 and is not enabled by this restructure.
- Action descriptions changed from `Face X` to `Select X`, `Choose X` to
  `Choose X from pack`, `Buy Joker and use immediately` to `Buy & use Joker`,
  and `model_turn` to `Model calls before game action`.

## 2026-09-20 — Game boundary (PR #28; `21f7486`)

- The game boundary uses typed sessions, public action contracts, private raw
  evidence, and explicit native settlement/error taxonomy. Previous native
  certificates require matching source and environment fingerprints.

## 2026-09-18 — Harness interface v7 (`aad111d`)

- v7 became the sole frozen harness interface. Prompt, guide, skills, provider
  capability, memory policy, and delivered limits are captured per episode;
  retired generations remain in the historical section below.

## 2026-09-16 — Release handoff (`31c0822`)

- The deployed implementation was
  `7f1796bdd1e9570d6acbbbfc677680031f4920facb2c1f894d908f69c3009518`;
  the environment was
  `ae2d734c8a56587033d6f4b131f2f3c068c5cad47979a1e19cd47f925682ac69`.
- The operator authorized $5 per run / $10 total including failed attempts in
  campaign `protocol-release-20260916`, with the shared durable ledger at
  `private/protocol-release-20260916/spending.json`. This records the historical
  authorization; it grants no new paid execution.
- Terra smoke episode `94495564883a4ed3a5a0a3944b3fb2f1` ended `GAME_LOSS` /
  `VERIFIED_ENGINE_TERMINAL`, with `evaluation_eligible: false`, 86 committed
  actions, $0.7715538 all-attempt cost, and no unresolved reservations.
- The rollback source/runtime/evidence set and logs were saved under
  `private/release-backup-20260916-B3/`. Read-only status is
  `uv run scripts/smoke_openai.py --status --campaign protocol-release-20260916`;
  it records review exposure and makes no provider call.
- Native action/replay/branch and browser evidence passed for that pinned
  configuration. Anthropic live compatibility, headless equivalence, new
  checkpoint scope, and scientific evaluation remained separate gates.

## 2026-09-16 — Implementation handoff (`2ab1730`)

- Part A defined durable campaign funding stops, model-specific reservations,
  episode-versus-campaign outcomes, rerun/crash recovery, and actual export
  coverage. B1–B3 shipped; the following backlog remains explicitly separate.
- B4: a UI-equivalent hand preview may expose only the selected hand's visible
  name and base chips/multiplier, never a final-score oracle, hidden-card
  inspector, move ranker, or selection search; native parity and nonmutation
  evidence are required.
- B5: context, memory, and note experiments remain hypotheses. Keep byte bounds
  separate from token limits and funding; never lower a byte threshold on the
  basis of measured tokenizer counts. Compare validity, coverage, costs, and
  full-run outcomes under matched conditions.
- B6: seed-bootstrap diagnostics and public offline CI shipped. Funding
  amendments and a separate native-release CI lane remain unimplemented;
  frozen batches still reject changed configuration. Research rubric changes
  remain versioned operator decisions, never retroactive relabeling.

## 2026-09-16 — Frozen agent protocol (`d7f4265`)

- `agent-protocol.json` freezes delivered prompt, knowledge, settings, and
  limits; legacy checkpoints missing a snapshot fail closed. Current rules
  live in `docs/harness.md`; historical prompt text remains below.

## 2026-09-16 — Defaults refactor (`9e51725`)

- Shared defaults have one Python owner, preset overrides, and explicit
  token/byte/cost distinctions; an override does not rewrite a frozen episode.

## 2026-09-16 — Campaign budget fix (`2ab1730`)

- Campaign funding interruption remains unresolved scheduling state; an
  episode-only cap remains a valid bounded non-win. Costs and unresolved slots
  stay visible in reports and public exports.

## 2026-09-16 — Dashboard performance and modifiers (`46e8096`)

- Status reads use cache invalidation at the run boundary, while card modifiers
  retain distinct visual treatment and current public prices.

## 2026-09-16 — Harness reliability (`f5ff6e3`)

- Settlement visibility, omitted-row behavior, absent-interest-as-zero, and
  the `ROUND_EVAL` close event remain explicit evidence invariants.

## 2026-09-15 — Harness efficiency audit (`f85b348`)

- **Question:** what measured input or budget friction should be addressed before
  another paid run? **Method:** audit three recorded journal/provider traces,
  usage categories, serialized component sizes, helper deliveries, and costs
  without reading seeds or opaque provider content. **Answer:** prioritize
  clearer offered-tag versus acquired-tag and monetary-headroom presentation;
  measure stable-prefix/cache changes separately. The traces do not establish
  that a larger prompt, more reasoning, or the harness caused an outcome.

## 2026-09-15 — Cache fix (`f85b348`)

- Prompt-cache handling remains bounded by provider usage categories and
  conservative reservations; old traces are not relabeled as savings evidence.

## 2026-09-15 — Cache diagnostics (`f85b348`)

- Recorded-request comparisons use an episode-local stable-prefix baseline;
  measured cache categories do not establish savings or authorize a paid probe.

## 2026-09-14 — Input reconciliation (`f85b348`)

- Reconciled the contract and supplied research around full native runs,
  public observations, prospective review, and certified interventions.
  Separate mechanics knowledge, trajectory exposure, and harness assistance;
  automatic horizon scores and broader research claims remained proposals.

## 2026-09-14 — Native mod feasibility (`f85b348`)

- Native mods were a feasibility path for public observations and explicit
  actions; calibration/debug capabilities remain outside the agent boundary.

## 2026-09-14 — Research reconciliation and return handoff (`f85b348`)

- The full operator decision/reconciliation and return handoff remain verbatim
  at `docs/research/reconciliation-2026-09-14.md` and
  `docs/research/return-handoff-2026-09-14.md`. Read both before changing
  research measures or annotation/navigation semantics.

## Harness interface history

Operator decision 2026-09-18: v7 became the sole harness interface. The current prompt now lives at `configs/prompts/harness.txt`; the retired generations remain here as exact historical text.

### operate_v1 — retired

Last prompt change: `f85b3483da5c59a930307b01a3f2dc244e12439c` (2026-09-15).

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

### tools_v2 — retired

Last prompt change: `f85b3483da5c59a930307b01a3f2dc244e12439c` (2026-09-15).

```text
Play this Balatro run using the available tools. Your objective is to maximize
the chance of the ordinary Ante 8 win within the interaction and inference budgets.
Choose your own strategy; money and intermediate scores are not separate objectives.

You receive the current board, the latest two public events, and your saved notes.
Use inspect_state for full public deck information or the latest 20 public events.
Use read_rules, read_history, and calculate whenever useful. These read-only tools
do not change the game, and you may use them before taking a game action. The
remaining helper allowance is shown in remaining_budget; at most eight per decision.
After inspect_state, the requested sections appear once at the observation paths
listed in its tool result. Repeated reads do not duplicate these sections in context.

Call a named gameplay tool when ready. Its arguments act on the current observation.
The game executes one action at a time and returns a settled state before the next
action. Available gameplay tools change with the phase. Unknown or invalid calls
do not execute; correct them using the returned feedback. There is no restart,
hidden-state inspection, future information, or automatic substitute move.

memory_update replaces your saved notes for future decisions (up to 4096 characters);
null keeps them unchanged. Context is rebuilt after each game action: only the
current board, bounded public history, and these explicit notes carry forward.
decision_note is optional, may be null, and is not graded.
```

#### Archived named-tools design record

The first Luna trace made 54 action attempts without using rules, history,
arithmetic, or memory. Four requests also exposed avoidable schema friction: two
placed a decision note at the wrong nesting level and two selected a blind that
was no longer current. This generation replaced the nested operation envelope
with named, strict, phase-aware tools; constrained IDs and selection counts to the
current public observation; made rules, history, arithmetic, and state inspection
explicit read-only helpers; and returned structured rejection feedback without
repairing or substituting an action.

The automatic context retained the current public board and two recent events.
Full masked deck information, the latest 20 events, and earlier public history
remained retrievable. Up to eight helpers were allowed per decision and 4,096
characters of explicit agent-authored memory crossed game-action boundaries.
Requests, omitted event IDs, helper results, invalid calls, and action outcomes
remained journaled; inspection could not access seeds, native saves, engine
internals, or future observations.

The first-request reconstruction reduced the recorded median JSON payload from
23,497.5 to 14,889 bytes. The native diagnostic committed 98 actions across 105
responses, including six inspections, before an oversized duplicated inspection
ended it with `REQUIRED_CONTEXT_EXCEEDS_LIMIT`. The follow-up delivered inspected
sections once, constrained current IDs, and reconstructed all 106 contexts within
the existing bound (largest 27,753 bytes). Mocked provider checks, native replay,
direct restoration, and immutable branching passed. These results established
transport and reconstruction behavior, not better play or live Anthropic
compatibility. The retired audit and recorded-context probe scripts were removed
with the interface.

Retired paid-smoke preset `configs/luna-tools-smoke.yaml`:

```yaml
# Native plumbing test; opt in with scripts/smoke_openai.py --allow-paid.
# Official standard prices verified 2026-09-14. No prompt caching or fast tier.
# Shared limits and disabled paid execution are inherited from config.py.
environment:
  stake: WHITE
budgets:
  max_episode_cost_usd: 1
  max_batch_cost_usd: 5
models:
  luna:
    provider: openai
    model: gpt-5.6-luna
    input_usd_per_million: 0.20
    output_usd_per_million: 1.20
    pricing_date: "2026-09-14"
    settings:
      harness_interface: tools_v2
      reasoning_effort: medium
      reasoning_summary: auto
```

### tools_v3 — retired

Last prompt change: `f85b3483da5c59a930307b01a3f2dc244e12439c` (2026-09-15).

```text
Play this Balatro run. Maximize the chance of the ordinary Ante 8 win within
the displayed budgets. Choose your own strategy; money and intermediate scores
are not separate objectives.

The focused view contains the current board, legal actions, resources, visible
blind effects and your saved notes. Card defaults are declared once in presentation. Counter defaults apply only
to cards marked counter_defaults_apply; absent counters otherwise remain unknown. Empty effects and
counters are omitted. All nondefault counters and active effects remain visible.
Hand levels are automatic while playing a hand and available on demand elsewhere.
Recent event text may be shortened; a truncated marker means more text is available.

Load information when needed: inspect_state reads one public section, read_history
browses past events, read_history_detail loads a complete past event, and
read_skill/read_rules load frozen knowledge. Inspection and history detail return
JSON text in UTF-8 byte pages; start at offset 0 and follow next_offset. Rule pages
use next_key. A page is only a fragment until complete is true. calculate supports
restricted arithmetic. Helpers do not advance the game; you may use them before
acting, up to eight per decision as shown by remaining_budget.

Only the latest three helper/error exchanges are kept, fewer when needed to fit
the context budget. retrieval_context lists cleared results and how to reload
them. Clearing context never deletes the journal. Save useful facts, plans and
reference keys in memory_update (up to 4096 characters); null keeps your notes.
After each game action, context is rebuilt and those notes are your only persistent
agent-authored memory. Do not assume earlier tool text is still loaded.

Call one named gameplay tool when ready, using the current observation ID and
visible object IDs. Card order is the displayed order; reorder changes it.
Invalid actions do not execute; use feedback to correct them. There is no restart,
hidden-state inspection, future information or automatic substitute move.
decision_note is optional, may be null, and is not graded.
```

#### Archived focused-context design record

This generation projected a compact current board while preserving active
effects, nondefault counters, concealed-card flags, current resources and action
constraints. Common defaults were declared once. Full public sections, hand
levels outside hand selection, event details, skills, and rules were loaded only
when requested; two recent event previews were visibly truncated at 240
characters when necessary.

State and history detail used lossless UTF-8 byte pages of at most 2,048 bytes.
History lookups were cut off at the model's current observation. The working
request retained the latest three helper/error exchanges, or fewer when needed,
and identified cleared receipts plus their reload operation. Original results
remained in the append-only journal. The 4,096-character explicit memory limit,
eight-helper allowance, provider limits, action limits, and spending ceilings did
not change.

Offline reconstruction covered 99 first requests, all 106 recorded contexts, and
2,544 additional guide reads. The matched median first-request size moved from
18,186 to 16,345 bytes; the largest guide-read reconstruction was 27,858 bytes.
The activation record reported 124 Python tests, Ruff, the Node 22.12 browser
build, three browser tests, repeated seed replay, direct restoration, and an
immutable branch. No paid provider call was made, and the size measurements did
not establish a gameplay improvement. The retired reconstruction audit was
removed with the interface.

Retired paid-smoke preset `configs/luna-focused-smoke.yaml`:

```yaml
# Native plumbing test; opt in with scripts/smoke_openai.py --allow-paid.
# Official standard prices verified 2026-09-14. No prompt caching or fast tier.
# Shared limits and disabled paid execution are inherited from config.py.
environment:
  stake: WHITE
budgets:
  max_episode_cost_usd: 1
  max_batch_cost_usd: 5
models:
  luna:
    provider: openai
    model: gpt-5.6-luna
    input_usd_per_million: 0.20
    output_usd_per_million: 1.20
    pricing_date: "2026-09-14"
    settings:
      harness_interface: tools_v3
      reasoning_effort: medium
      reasoning_summary: auto
```

### tools_v4 — retired

Last prompt change: `f85b3483da5c59a930307b01a3f2dc244e12439c` (2026-09-15).

```text
Play this Balatro run. Maximize the chance of the ordinary Ante 8 win within
the displayed budgets. Choose your own strategy; money and intermediate scores
are not separate objectives.

The focused view contains the current board, legal actions, resources, visible
blind effects and your saved notes. Card defaults are declared once in presentation. Counter defaults apply only
to cards marked counter_defaults_apply; absent counters otherwise remain unknown. Empty effects and
counters are omitted. All nondefault counters and active effects remain visible.
Hand levels are automatic while playing a hand and available on demand elsewhere.
Recent event text may be shortened; a truncated marker means more text is available.

Load information when needed: inspect_state reads one public section, read_history
browses past events, read_history_detail loads a complete past event, and
read_skill/read_rules load frozen knowledge. Inspection and history detail return
JSON text in UTF-8 byte pages; start at offset 0 and follow next_offset. Rule pages
use next_key. A page is only a fragment until complete is true. calculate supports
restricted arithmetic. Helpers do not advance the game; you may use them before
acting, up to eight per decision as shown by remaining_budget.

Only the latest three helper/error exchanges are kept, fewer when needed to fit
the context budget. retrieval_context lists cleared results and how to reload
them. Clearing context never deletes the journal. Save useful facts, plans and
reference keys in memory_update (up to 4096 characters); null keeps your notes.
After each game action, context is rebuilt and those notes are your only persistent
agent-authored memory. Do not assume earlier tool text is still loaded.

Call one named gameplay tool when ready, using the current observation ID and
visible object IDs. Card order is the displayed order; reorder changes it.
Invalid actions do not execute; use feedback to correct them. There is no restart,
hidden-state inspection, future information or automatic substitute move.
decision_note is optional, may be null, and is not graded.

The tool catalog is fixed. Only gameplay tools named in the current observation's available_action_types are permitted now. Use its observation_id, current visible object IDs, and action_constraints for legal selections, counts, targets, and reorder areas. Tools for other phases do not become legal merely because their schemas are present.
```

Retired paid-smoke preset `configs/terra-cache-probe.yaml`:

```yaml
# Recorded public decisions only; does not authorize spending or launch Balatro.
# Shared limits, guide and disabled paid execution are inherited from config.py.
budgets:
  max_provider_calls: 4
  max_transport_attempts: 1
  max_output_tokens_per_call: 4096
  max_episode_cost_usd: 1
  max_batch_cost_usd: 1
models:
  terra-cache:
    provider: openai
    model: gpt-5.6-terra
    input_usd_per_million: 2
    cached_input_usd_per_million: 0.2
    cache_write_input_usd_per_million: 2.5
    output_usd_per_million: 12
    pricing_date: '2026-09-15'
    settings:
      reasoning_effort: medium
      reasoning_summary: auto
      harness_interface: tools_v4
```

### tools_v5 — retired

Last prompt change: `d908283dc525d4120330cb85c20d624cb4c8d323` (2026-09-16).

```text
Play this Balatro run. Maximize the chance of the ordinary Ante 8 win within
the displayed budgets. Choose your own strategy; money and intermediate scores
are not separate objectives.

The focused view contains the current board, legal actions, resources, visible
blind effects and your saved notes. Card defaults are declared once in presentation. Counter defaults apply only
to cards marked counter_defaults_apply; absent counters otherwise remain unknown. Empty effects and
counters are omitted. All nondefault counters and active effects remain visible.
Hand levels are automatic while playing a hand and available on demand elsewhere.
Recent event text may be shortened; a truncated marker means more text is available.

Load information when needed: inspect_state reads one public section, read_history
browses past events, read_history_detail loads a complete past event, and
read_skill/read_rules load frozen knowledge. Inspection and history detail return
JSON text in UTF-8 byte pages; start at offset 0 and follow next_offset. Rule pages
use next_key. A page is only a fragment until complete is true. calculate supports
restricted arithmetic. Helpers do not advance the game; you may use them before
acting, up to eight per decision as shown by remaining_budget.

Only the latest three helper or error results stay loaded, fewer when needed to
fit the context budget. retrieval_context lists cleared results and how to reload
them. Provider protocol blocks may remain around a cleared-result receipt so the
same decision can continue safely; the old result itself is no longer loaded.
Clearing context never deletes the journal. Save useful facts, plans and reference
keys in memory_update (up to 4096 characters); null keeps your notes. After each
game action, context is rebuilt and those notes are your only persistent
agent-authored memory. Do not assume earlier tool text is still loaded.

Call one named gameplay tool when ready, using the current observation ID and
visible object IDs. Card order is the displayed order; reorder changes it.
Invalid actions do not execute; use the specific feedback to correct them. There
is no restart, hidden-state inspection, future information or automatic substitute
move. decision_note is optional, may be null, and is not graded.

The tool catalog is fixed. Only gameplay tools named in the current observation's
available_action_types are permitted now. Use its observation_id, current visible
object IDs, and action_constraints for legal selections, counts, targets, and
reorder areas. Tools for other phases do not become legal merely because their
schemas are present.

Use current_costs from the current observation for transaction prices. Item
descriptions explain effects; they do not establish whether a limited-use benefit
remains available. Quotes refresh after every settled action, including pack
returns. A null price or remaining-use count is unknown, not zero.
cash_cost is the upfront quote; recorded rental obligations are separate.
Affordability does not imply purchase eligibility. Mode checks marked
requires_targets need a valid target selection before execution.
last_action.transaction separates the quoted charge from cash before/after.
An unobserved actual charge remains null; net cash change can include other effects.

<!-- BEGIN ALWAYS-LOADED.md -->
# Mechanics to retain across decisions

These are ordinary defaults. Current public effects can modify or
override them.

- Ordinary interest pays $1 per full $5 of eligible cash at settlement,
  capped at $5. Eligible balances below $5 earn no ordinary interest.
  Active deck effects, vouchers, and other public effects can change this.
- Ordinary unused hands pay $1 each at settlement.
- Borrowing capacity permits spending below $0; it is not additional
  cash. Money received while your balance is negative first reduces
  that negative balance.
- Score beyond the current blind's target does not carry forward.
- Only scoring cards normally add card chips and trigger "when scored"
  effects. In Three of a Kind A-A-A-K-Q, only the Aces score; the added
  King and Queen contribute neither.
- Money, deck changes, Jokers, and hand upgrades persist across rounds,
  subject to their effects.
- Skipping a blind forgoes its normal played-round settlement and shop;
  the displayed skip tag provides its own reward.
<!-- END ALWAYS-LOADED.md -->
```

#### Archived provider-continuation design record

This generation treated all helper calls before one game action as a single
decision. OpenAI requests remained stateless (`store: false`) while returning
complete response output items, including encrypted reasoning state, ahead of
matching `function_call_output` items. Anthropic requests returned the complete
assistant content array—including thinking, redacted-thinking, text, and tool-use
blocks—followed immediately by linked tool results. A committed game action reset
the provider-native continuation; only public history and bounded explicit memory
crossed that boundary.

Both providers received one fixed strict tool catalog with local phase
availability checks and automatic tool choice. Feedback distinguished no call,
multiple calls, unavailable tools, invalid JSON, schema failures, incomplete or
context-limited responses, refusals, and other unsafe statuses. Every call ID in
a multi-call response received a linked error result. Upstream error text was not
copied into feedback.

The latest three helper/error results stayed loaded. Cleared results left a small
reload receipt while required provider protocol shells stayed intact; if those
shells could not fit, construction failed explicitly. Public exports removed
encrypted or signed opaque provider material and retained an omission marker.
Offline tests covered exact round trips for both providers, continuation reset,
context clearing, response classification, cache accounting, and export
sanitization. They used mocked transports and synthetic mechanics and therefore
did not establish live billing, gameplay quality, or native fidelity.

### tools_v6 — retired

Last prompt change: `59971ed704879a9ed07d04a8e932d7941a0afa24` (2026-09-17).

```text
Play this Balatro run. Maximize the chance of the ordinary Ante 8 win within
the displayed budgets. Choose your own strategy; money and intermediate scores
are not separate objectives.

The focused view contains the current board, legal actions, resources, visible
blind effects and your saved notes. Card defaults are declared once in presentation. Counter defaults apply only
to cards marked counter_defaults_apply; absent counters otherwise remain unknown. Empty effects and
counters are omitted. All nondefault counters and active effects remain visible.
Hand levels are automatic while playing a hand and available on demand elsewhere.
Recent event text may be shortened; a truncated marker means more text is available.

Load information when needed: inspect_state reads one public section, read_history
browses past events, read_history_detail loads a complete past event, and
read_skill/read_rules load frozen knowledge. Inspection and history detail return
JSON text in UTF-8 byte pages; start at offset 0 and follow next_offset. Rule pages
use next_key. A page is only a fragment until complete is true. calculate supports
restricted arithmetic. Helpers do not advance the game; you may use them before
acting, up to the per-decision allowance shown by remaining_budget.

Only the latest three helper or error results stay loaded, fewer when needed to
fit the context budget. retrieval_context lists cleared results and how to reload
them. Provider protocol blocks may remain around a cleared-result receipt so the
same decision can continue safely; the old result itself is no longer loaded.
Clearing context never deletes the journal. Your run_notebook is automatically
included in every request, including between helper calls. You may save useful
run-specific observations, calculations, plans and uncertainties with
set_run_note(key,text), or delete an entry with delete_run_note(key). Keys are
yours to choose (1-64 characters, no control characters). The total Unicode
character count of keys plus text is limited by run_notebook.character_limit.
Oversized edits leave all existing notes unchanged. Edits consume a helper call
but do not advance the game. Current observations remain authoritative; saved
notes are your own records, not verified game facts. Note-taking is optional.
After each game action, context is rebuilt; your notebook is your only persistent
agent-authored memory. Gameplay actions do not accept memory_update.
Do not assume earlier tool text is still loaded.

last_action automatically reports your latest gameplay action's observed changes.
retrieve_action_result retrieves a committed action and its receipt by decision_id;
null selects the latest. Use section before/after for the recorded public conditions.
A returned episode_id identifies inherited actions; historical handles are not
current action targets. Follow next_offset as byte_offset for complete JSON.
The recorded_decision_note is your original note, not a verified prediction.
State changes are not a causal scoring breakdown; a null hand_score is unknown.
Helpers and note writes never replace the last gameplay action.

permitted_tools lists the tools permitted on this request. When no helpers remain,
choose a permitted gameplay action or abort_run. Prohibited helper requests receive
feedback and count toward the bounded invalid-response limit. The harness never
substitutes a gameplay action.

Call one named gameplay tool when ready, using the current observation ID and
visible object IDs. Card order is the displayed order; reorder changes it.
Invalid actions do not execute; use the specific feedback to correct them. There
is no restart, hidden-state inspection, future information or automatic substitute
move. decision_note is optional, may be null, and is not graded.

The tool catalog is fixed. Only tools in permitted_tools may be used. Gameplay
tools must also be named in the current observation's available_action_types.
Use its observation_id, current visible
object IDs, and action_constraints for legal selections, counts, targets, and
reorder areas. Tools for other phases do not become legal merely because their
schemas are present.

Use current_costs from the current observation for transaction prices. Item
descriptions explain effects; they do not establish whether a limited-use benefit
remains available. Quotes refresh after every settled action, including pack
returns. A null price or remaining-use count is unknown, not zero.
cash_cost is the upfront quote; recorded rental obligations are separate.
Affordability does not imply purchase eligibility. Mode checks marked
requires_targets need a valid target selection before execution.
last_action.transaction separates the quoted charge from cash before/after.
An unobserved actual charge remains null; net cash change can include other effects.

<!-- BEGIN ALWAYS-LOADED.md -->
# Mechanics to retain across decisions

These are ordinary defaults. Current public effects can modify or
override them.

- Ordinary interest pays $1 per full $5 of eligible cash at settlement,
  capped at $5. Eligible balances below $5 earn no ordinary interest.
  Active deck effects, vouchers, and other public effects can change this.
- Ordinary unused hands pay $1 each at settlement.
- Borrowing capacity permits spending below $0; it is not additional
  cash. Money received while your balance is negative first reduces
  that negative balance.
- Score beyond the current blind's target does not carry forward.
- Only scoring cards normally add card chips and trigger "when scored"
  effects. In Three of a Kind A-A-A-K-Q, only the Aces score; the added
  King and Queen contribute neither.
- Money, deck changes, Jokers, and hand upgrades persist across rounds,
  subject to their effects.
- Skipping a blind forgoes its normal played-round settlement and shop;
  the displayed skip tag provides its own reward.
<!-- END ALWAYS-LOADED.md -->
```

#### Archived run-notebook design and deployment record

This generation replaced whole-string memory with optional keyed notebook edits.
Keys contained 1–64 Unicode characters without ASCII controls, and the combined
key/text content retained the existing 4,096-character allowance. Accepted edits
were journaled with predecessor and new revisions plus a content hash; oversized
edits were atomic failures. The notebook was delivered on every request, remained
separate from helper-retention pruning, and was explicitly described as
agent-authored rather than authoritative game state.

`retrieve_action_result` paged the public receipt or before/after observation for
a committed action. Episode and decision identifiers disambiguated inherited
history, while helpers, note edits, rejected calls, and aborts did not become the
latest gameplay action. The retrieval surface exposed no seed, hidden draw order,
engine state, provider-private continuation, or invented score breakdown.

The per-decision helper allowance covered reads, arithmetic, and note edits. At
zero, only current game actions and abort remained permitted; repeated extra
helper requests ended as `AGENT_PROTOCOL_FAILURE` rather than receiving a
substitute action. Checkpoints stored the pre-decision notebook, branches inherited
only events before their boundary, and prospective review withheld note edits
until action reveal.

The 2026-09-17 offline gate reported 394 Python tests, 18 browser tests, Ruff,
TypeScript, diff checks, and a production build, with nine native gates skipped.
A subsequent unpaid deployment used 12 planned native process launches covering
profile stability, seed-prefix replay, direct checkpoint restoration, branching,
settlement evidence, doctor, and browser review. It made no paid provider request
and did not establish gameplay or cost improvement. The former sync option and
deployment instructions for selecting this retired interface were removed.

### tools_v7 — current

The authoritative current prompt is [`configs/prompts/harness.txt`](configs/prompts/harness.txt),
last changed on main by `5ca5f6e` (PR #34, 2026-09-20). Its placeholders are
resolved from the frozen episode configuration; a rendered historical snapshot
must not be presented as the current template. Retired versions above retain
their exact historical text.
