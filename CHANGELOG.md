# Changelog

## Dashboard cutover (issue #15)

- Split the dashboard into a typed client, screen components, an opt-in
  workbench, and Python gateway route/factory modules.
- Add server-owned decision exports, shared action descriptors, loopback bind
  protection, flag-off 404 behavior, and budget continuation admission.
- Place DevTrace under Decision Explorer and preserve the existing accessible
  browser test labels and review flow.

## Game boundary cutover (issue #8)

- Split the native adapter into `game/` contracts, state, actions, transport,
  environment, and sessions; preserve the scripted fake and move certification
  and source fingerprints to `evidence/`.
- Error taxonomy: Lua infrastructure and harness faults are no longer scored
  as agent illegality (spec §6.2). Only enumerated Lua codes appear as public
  reasons; unrecognized endpoint text stays in private evidence.
- A rejected request-ledger status with a missing or malformed response is now
  infrastructure failure, not agent illegality; its outcome is unverified.
- Unify native and upstream mutation settlement at 30 transitions plus 10
  consecutive ready frames. Prior native certificates require re-certification.

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

### tools_v7 — current as `harness.txt`

Last prompt change: `4d64ff892cc90fdfaffad6179c2b35ff549afc0b` (2026-09-17).

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
included in every request, including between helper calls. Maintain a concise
notebook of your current plan, useful conclusions from prior actions and
calculations, and unresolved questions. Update it when these change; remove
obsolete entries. Avoid copying facts already in the current observation or
unchanged notes. Preserve useful findings before their supporting context leaves
the active window. Choose your own note keys and strategy. Save entries with
set_run_note(key,text), or delete an entry with delete_run_note(key). Keys are
yours to choose (1-64 characters, no control characters). The total Unicode
character count of keys plus text is limited by run_notebook.character_limit.
Oversized edits leave all existing notes unchanged. Edits consume a helper call
but do not advance the game. Current observations remain authoritative; saved
notes are your own records, not verified game facts.
You can also attach one edit to a gameplay call with note_update: {key,text} sets
a note; {key,text:null} deletes it; null leaves notes unchanged. This uses no
additional helper call. Both the action and edit are validated before either
executes. A valid edit is journaled before game execution and survives an engine
failure; it does not claim the action succeeded. Gameplay actions do not accept
memory_update. There is no requirement to write an unchanged note on every turn.

working_memory automatically carries a bounded set of completed decisions:
actions, recorded decision notes, observed results, and recent helper receipts.
These are historical records. Their handles, quotes and counters are NOT current
action targets or prices. A recorded decision note is an agent account, not a
verified conclusion. Current observations and current_costs remain authoritative.
Retention limits and omitted counts are explicit; older records remain in the
journal. Use retrieve_action_result/read_history for missing public action
evidence. Historical inspection arguments do not reload the old state: inspect_state
always reads the CURRENT observation. Frozen rules/skills and arithmetic can be
requested again. Opaque provider continuation does not cross game actions.

notebook_maintenance warns when the next action or helper may clear older context.
Save useful conclusions while they are still loaded, using a note helper or
note_update on the action. At a new round or a change of plan, check whether your
notes still reflect what you intend. Keep uncertainty explicit. The notebook and
retained recent history are separate: history may expire; notes persist until edited.
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
