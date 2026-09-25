# Harness

`tools_v8` is the single active interface. Before its first decision, a run freezes the prompt,
rules guide, skills catalog, provider capability, memory policy, model/pricing settings and limits.
Immutable `agent-protocol.json` binds checkpoints and ordinary branches to that snapshot.
Missing snapshots fail with `AGENT_PROTOCOL_SNAPSHOT_MISSING`; retired interfaces fail with
`AGENT_PROTOCOL_INTERFACE_RETIRED`. Neither is rewritten. Run-level [Restore](dashboard.md#restore-unfinished-runs)
may accept older source through an explicit immutable compatibility receipt: the worker binds
the accepted executor separately, preserving historical source identity, model, knowledge and limits.
Prior calls and retained reservations count across every restoration attempt.

## What the model receives

Each decision contains the current public observation, bounded public history and explicit working memory.
The twelve named skills load on-demand chapter pages without mutating frozen knowledge. Rules, history,
inspection and arithmetic are read-only. Regeneration is a new bounded request, not a hidden retry or alternative action.

| Surface | Delivery and preservation contract |
| --- | --- |
| Object IDs | Positive integers, not array positions: assigned on first public appearance, never reused, stable across moves. Concealment never reconnects hidden identities. The backend resolves canonical opaque handles before unchanged phase, observation, count and legality checks; unknown IDs, strings, booleans and stale targets are refused. |
| Episode IDs | Separate integer namespace for historical lookups; decision numbers and pagination offsets retain their meaning. Restore rebuilds both tables from selected public ancestry. |
| Audit and history | No audit/event hashes, notebook checksums or event UUIDs; omitted history is a count. Retrieval uses offsets or episode/decision references. Complete summaries are projected before truncation; inspection/history pages are projected before UTF-8 pagination, so cursors address delivered text. |
| Current prices | `observation.state.offers` co-locates facts and each `quote`: upfront `cash_cost`, affordability, purchase-mode checks, target bounds and recorded obligations. Only equal duplicate labels/effects/prices are omitted; unequal values remain. `current_costs` keeps balances, credit, inventory, reroll and sale quotes. Null is unknown, zero is zero; rental obligations are not upfront costs. Canonical costs are unchanged; full history/inspection keeps original public prices, not today's quote. |
| Receipts | Only an exact duplicate of `observation.last_action` becomes `observed_result_ref: "/observation/last_action"`: one absolute, single-hop reference to a complete value in this request, not a general reference language. Comparison precedes audit-field removal and preserves types, missing/null distinctions and array order. Standalone pages remain complete; pruning cannot leave a dangling reference. |
| Evidence | Canonical observation/action journals remain unchanged. New helper receipts use projected IDs for every policy; checkpoints' working-memory helper results retain those same integers and refold consistently. Existing journals and checkpoint bytes are never rewritten. Notebook text, previous-action provenance and observed/quoted charges remain distinct. The inspector shows delivered offers, the reference and its latest-receipt target. |
| Verbatim content | Notebook text, rules, effects, counters and provider-native continuation blocks stay intact. Notebook-edit and target-selection instructions appear once in the shared prompt, not in every gameplay schema. |

V8 changes frozen protocol identity, not existing runs: v7/pre-compaction runs need their retained executor;
the source-compatibility gate refuses silently changing their request format. Native evidence can be reused
independently when native interface/runtime bytes are identical.

The defaults below come from `config.py`; bytes and provider tokens are separate
units. Overrides are recorded and cannot silently widen a frozen episode.

| Setting or constant | Default |
| --- | ---: |
| `max_input_tokens_per_call` / `DEFAULT_INPUT_TOKEN_LIMIT` | 32,768 tokens |
| `max_request_bytes` / `DEFAULT_REQUEST_BYTE_LIMIT` | 262,144 bytes |
| `INPUT_TOKEN_SAFETY_MARGIN` | 512 tokens |
| `CONTEXT_FRAMING_BYTES` | 4,096 bytes |
| `HELPER_PAGE_BYTES` | 2,048 bytes |
| `RETAINED_HELPER_RESULTS` | 3 results |
| `WORKING_MEMORY_DECISIONS` | 3 completed decisions |
| `WORKING_MEMORY_BYTES` | 24,576 bytes |
| `ALWAYS_LOADED_MAX_BYTES` | 1,024 bytes |
| `max_game_actions` | 1,500 |
| `max_provider_calls` | 2,000 |
| `max_helper_calls_per_decision` | 8 |
| `max_output_tokens_per_call` | 8,192 tokens |
| `max_transport_attempts` | 3 |

Trim the oldest working-memory frame, then a loaded helper result, then a public event; if none remains,
fail with `LOCAL_CONTEXT_LIMIT`. Never trim the current observation, notebook or action constraints.
Frozen `knowledge.json` retains rules/skills; `uv run bh guide package` regenerates the guide for future episodes.
`ActionEnvelope` pairs the current observation ID with one typed action and may carry a memory replacement
and decision note. Validate phase, handles, counts and order before dispatch. Never send raw RPC, seeds,
saves, endpoint text or private provider credentials to the model.

## Providers

OpenAI Responses and Anthropic Messages share a capability contract; transport doubles use the session seam.
Provider-native reasoning/tool blocks continue within one decision, resetting after the action. Every request,
including retries, reserves configured ceilings/prices. Unknown usage stays reserved; record overage and stop
the next request at the applicable episode/campaign cap. Paid calls require operator enablement, settings,
and episode/batch caps. Anthropic `temperature` is incompatible with manual `thinking_budget`.

Preview persistent credentials with `uv run bh credentials --source /path/to/private/provider.env`;
add `--apply` to write. Source/destination must be native Linux paths. Only provider key names are accepted;
output omits values/private paths. Owner-only (0600) `private/providers.env` is referenced by a user-service drop-in.
Without `--source`, preserve credentials or prepare an empty unpaid-dashboard placeholder; no mode calls a provider.
Check both destinations before replacing either. Credentials and session registration share `storage/private_files.py`:
refuse file/ancestor symlinks, non-directory parents and nonregular destinations; new private parents start 0700.
Session apply checks registration and worker-lock paths before creating the lock; failures remain
`WINDOWS_SESSION_REGISTRATION_FAILED`. These preflights do not prevent same-user concurrent path swaps.
Later I/O failures can leave partial apply: exit nonzero with `CREDENTIAL_APPLY_INCOMPLETE`, a sanitized reason,
and separate `credentials_written` / `drop_in_written` booleans. These describe completed replacements, not
a two-file atomic transaction. Repair the destination and explicitly retry; without `--source`, retain written
credentials. Complete apply, run `systemctl --user daemon-reload`, and restart `balatro-horizons.service` only
while the worker and native verifier are idle. Never commit credentials or put their contents/paths in support logs.

Prompt caching is an accounting concern, not an unverified optimization:
ordinary input, cache reads, cache writes, output, reservations, and settlement
remain distinct categories. A cache diagnostic comparison is metadata-only and
must use an episode-local baseline; it does not authorize a live probe or expose
opaque provider content.
The stable-prefix breakpoint precedes dynamic state, budgets, memory, and helper
outputs. `comparison_response_id` advances only after a completed response with
a nonempty ID; a new episode or branch starts without a baseline. Diagnostics
are best effort; actual usage establishes cache read/write charges. Reserve the
highest configured input rate, and keep the full reservation on inconsistent
usage rather than double-counting disjoint cache categories.

### GPT-6 Sol and Luna

Opt-in [gpt6-sol-smoke.yaml](../configs/gpt6-sol-smoke.yaml) and [gpt6-luna-smoke.yaml](../configs/gpt6-luna-smoke.yaml)
leave existing models and smoke defaults unchanged. Select the matching alias, for example
`bh smoke --config configs/gpt6-luna-smoke.yaml --agent luna6 --campaign gpt6-luna-smoke --dry-run`.
This preflight neither authorizes paid execution nor certifies native runtime. Both models use standard-tier,
stateless, explicit-cache Responses; efforts are `none`, `low`, `medium` (default), `high`, `xhigh`, `max`.
`minimal` is refused; `temperature` requires explicit `none`. Standard short-context USD/million, verified 2026-09-22:

| Model | Input | Cache read | Cache write | Output |
| --- | ---: | ---: | ---: | ---: |
| GPT-6 Sol | 2.00 | 0.20 | 2.50 | 10.00 |
| GPT-6 Luna | 0.10 | 0.01 | 0.125 | 0.50 |

Sources: [Sol](https://developers.openai.com/api/docs/models/gpt-6-sol), [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
[pricing](https://developers.openai.com/api/docs/pricing). Input limits above 272,000 tokens fail closed: long-context pricing is unconfigured.
Use `bh human register` with a new alias, an existing model to clone and all four rates. Preview first;
`--apply` saves without starting a run or changing budgets.

## Money

Public cost fields distinguish `public_costs_v2` from `public_transaction_v1`.
They expose sanitized cost, balance, reservation, and settlement facts without
provider secrets or hidden accounting. Every loop receives an explicit `Spending`
ledger; all original batch attempts share one. Standalone ledgers use the configured
campaign cap, but have no scheduler or `scheduling_stop`. In a batch, campaign-only
refusal leaves the slot unresolved; episode-only refusal is a valid non-win.
Funding stops are durable and cannot be resumed by rerunning a frozen batch.
The admission predicate is `total + reservation <= cap`, using the next model's
worst-case reservation and unchanged floating-point arithmetic.
`CAMPAIGN_COST_CAP` yields `CAMPAIGN_INTERRUPTED`; `EPISODE_COST_CAP` and
`EPISODE_AND_CAMPAIGN_COST_CAP` yield `BUDGET_EXHAUSTED`. The latter still stops
later campaign scheduling while the current slot is a valid bounded non-win.
`EPISODE_CAP_BELOW_RESERVATION` is a configuration refusal before episode/game
creation. A write-once `stop.json` preserves the first scheduling stop; changing
a frozen batch configuration yields `BATCH_CONFIGURATION_CHANGED`.
`provider_reservation` is private accounting evidence, omitted from public exports.
Retained reservation dollars still appear in terminal summaries, batch
`all_attempt_cost_usd`, and status costs; private events do not hide spending.
Reserve under the lock immediately before each send, including retries, and journal
the reservation before the request. Successful responses settle measured costs;
provider failures call `retain(request_id)`. Preflight is only a locked snapshot,
never a substitute for authoritative reservation at send time.
Only campaign reasons may enter `stop.json`. Preflight permits only
`CAMPAIGN_COST_CAP`, with no episode, terminal or outcome. Episode-stage stops
require an episode ID, terminal reference and matching reason/outcome.
An unfunded paid batch slot creates no episode or game; first-stop bytes survive
restarts. `REFUSAL_OUTCOMES` is the single reason/outcome vocabulary.

## Journals, branches, and tests

Requests, helper receipts, rejections, actions, outcomes, memory edits, and
budget settlements are hash-chained append-only records. A state-checked branch
inherits frozen protocol and knowledge but gets a new immutable identity. Human
and assisted branches are diagnostic and excluded from autonomous scores.
Offline fakes are application tests only. Native reuse requires an explicit
offline report and matching certificate; see [evidence](evidence.md).
