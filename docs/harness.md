# Harness

The current harness is one interface and one frozen protocol. A run snapshots
the prompt, rules guide, skills catalog, provider capability, memory policy,
model/pricing settings, and limits before its first decision. Later edits do
not change that episode or its ordinary branches.
The immutable `agent-protocol.json` binds checkpoints to that snapshot. A legacy
checkpoint without it fails with `AGENT_PROTOCOL_SNAPSHOT_MISSING`; it is never
rewritten. Retired interface snapshots fail with `AGENT_PROTOCOL_INTERFACE_RETIRED`.

## What the model receives

Each decision contains the current public observation, bounded public history,
and explicit working memory. The model may use the twelve named skills through
on-demand chapter pages, but paging cannot mutate the frozen knowledge base.
Rules, history, inspection, and arithmetic are read-only helpers. Regeneration
is a new bounded request, not a hidden retry or an alternative action.

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

To fit the context, trim the oldest working-memory frame, then a loaded helper
result, then a public event; if nothing removable remains, fail with
`LOCAL_CONTEXT_LIMIT`. Never trim the current observation, notebook, or action
constraints. The frozen `knowledge.json` retains the rules/skills; regenerate
the guide for future episodes with `uv run scripts/package_balatro_guide.py`.

`ActionEnvelope` pairs the current observation ID with one typed action and may
carry a memory replacement and decision note. The harness validates phase,
handles, counts, and order before dispatch. The model never receives raw RPC,
seeds, saves, endpoint text, or private provider credentials.

## Providers

OpenAI Responses and Anthropic Messages implement the same capability contract;
transport doubles live behind the session seam. Provider-native reasoning and
tool blocks continue within one decision and reset after the game action. Every
request reserves configured ceilings and prices, including retries. Unknown
usage remains reserved; overage is recorded; the next request stops at the
episode cap. A paid call requires explicit operator enablement, model settings,
and episode and batch caps.
Anthropic `temperature` is incompatible with manual `thinking_budget`.

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

## Money

Public cost fields distinguish `public_costs_v2` from `public_transaction_v1`.
They expose sanitized cost, balance, reservation, and settlement facts without
provider secrets or hidden accounting. The campaign cap is separate from the
episode cap: a campaign-only refusal leaves the current slot unresolved and
records `scheduling_stop`; an episode-only refusal is a valid
`BUDGET_EXHAUSTED` non-win. Funding stops are durable and cannot be resumed by
rerunning a frozen batch.
The admission predicate is `total + reservation <= cap`, using the next model's
worst-case reservation and unchanged floating-point arithmetic.
`CAMPAIGN_COST_CAP` yields `CAMPAIGN_INTERRUPTED`; `EPISODE_COST_CAP` and
`EPISODE_AND_CAMPAIGN_COST_CAP` yield `BUDGET_EXHAUSTED`. The latter still stops
campaign scheduling. Standalone ledgers retain the configured campaign cap.
`EPISODE_CAP_BELOW_RESERVATION` is a configuration refusal before episode/game
creation. A write-once `stop.json` preserves the first scheduling stop; changing
a frozen batch configuration yields `BATCH_CONFIGURATION_CHANGED`.
`provider_reservation` is private accounting evidence, omitted from public exports.

## Journals, branches, and tests

Requests, helper receipts, rejections, actions, outcomes, memory edits, and
budget settlements are hash-chained append-only records. A certified branch
inherits frozen protocol and knowledge but gets a new immutable identity. Human
and assisted branches are diagnostic and excluded from autonomous scores.
Offline fakes are application tests only. Native reuse requires an explicit
offline report and matching certificate; see [evidence](evidence.md).
