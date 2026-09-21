# Harness

The current harness is one interface and one frozen protocol. A run snapshots
the prompt, rules guide, skills catalog, provider capability, memory policy,
model/pricing settings, and limits before its first decision. Later edits do
not change that episode or its ordinary branches.

## What the model receives

Each decision contains the current public observation, bounded public history,
and explicit working memory. The model may use the twelve named skills through
on-demand chapter pages, but paging cannot mutate the frozen knowledge base.
Rules, history, inspection, and arithmetic are read-only helpers. Regeneration
is a new bounded request, not a hidden retry or an alternative action.

The shared defaults are 32,768 input tokens, 262,144 request bytes, 512 safety
tokens, a 4,096-token context frame, 2,048-token helper pages, three retained
helper results, three completed decisions, and 24,576 bytes of working memory.
Per-episode limits are 1,500 game actions, 2,000 requests, eight helpers per
decision, 8,192 output tokens, and three transport attempts. Configuration
overrides are recorded and cannot widen an already frozen episode silently.

`ActionEnvelope` pairs the current observation ID with one typed action and may
carry a memory replacement and decision note. The harness validates phase,
handles, counts, and order before dispatch. The model never receives raw RPC,
seeds, saves, endpoint text, or private provider credentials.

## Providers and spending

OpenAI Responses and Anthropic Messages implement the same capability contract;
transport doubles live behind the session seam. Provider-native reasoning and
tool blocks continue within one decision and reset after the game action. Every
request reserves configured ceilings and prices, including retries. Unknown
usage remains reserved; overage is recorded; the next request stops at the
episode cap. A paid call requires explicit operator enablement, model settings,
and episode and batch caps.

Prompt caching is an accounting concern, not an unverified optimization:
ordinary input, cache reads, cache writes, output, reservations, and settlement
remain distinct categories. A cache diagnostic comparison is metadata-only and
must use an episode-local baseline; it does not authorize a live probe or expose
opaque provider content.

Public cost fields distinguish `public_costs_v2` from `public_transaction_v1`.
They expose sanitized cost, balance, reservation, and settlement facts without
provider secrets or hidden accounting. The campaign cap is separate from the
episode cap: a campaign-only refusal leaves the current slot unresolved and
records `scheduling_stop`; an episode-only refusal is a valid
`BUDGET_EXHAUSTED` non-win. Funding stops are durable and cannot be resumed by
rerunning a frozen batch.

## Journals, branches, and tests

Requests, helper receipts, rejections, actions, outcomes, memory edits, and
budget settlements are hash-chained append-only records. A certified branch
inherits frozen protocol and knowledge but gets a new immutable identity. Human
and assisted branches are diagnostic and excluded from autonomous scores.
Offline fakes are application tests only. Native reuse requires an explicit
offline report and matching certificate; see [evidence](evidence.md).
