# Harness

`tools_v8` is the single active interface. Before its first decision, a run freezes the prompt,
selected deck/stake, rules guide, skills catalog, provider capability, memory policy,
model/pricing settings and limits.
Immutable `agent-protocol.json` binds checkpoints and ordinary branches to that snapshot.
Missing snapshots fail with `AGENT_PROTOCOL_SNAPSHOT_MISSING`; retired interfaces fail with
`AGENT_PROTOCOL_INTERFACE_RETIRED`. Neither is rewritten. Run-level [Restore](dashboard.md#restore-unfinished-runs)
may accept older source through an explicit immutable compatibility receipt: the worker binds
the accepted executor separately, preserving historical source identity, model, knowledge and limits.
Prior calls and retained reservations count across every restoration attempt.

New runs record `context_policy: append_only_decision_v1` and a provider-native
wire policy (`openai_responses_v1` or `anthropic_messages_v1`). This changes
within-decision retention, not the `tools_v8` game interface. Older context
policies require their retained executor; native evidence reuse does not make
their model conversation compatible with this release.

## What the model receives

Each decision contains the current public observation, bounded public history and explicit working memory.
The twelve named skills load on-demand chapter pages without mutating frozen knowledge. Rules, history,
inspection and arithmetic are read-only. Regeneration is a new bounded request, not a hidden retry or alternative action.

| Surface | Delivery and preservation contract |
| --- | --- |
| Run configuration | New runs append `Run configuration: deck=PLASMA; stake=ORANGE.` (with their selected names) to the frozen rules prefix, alongside an immutable `run_configuration` pair. Both provider formats deliver it before the first decision; it stays identical across actions and helpers, before OpenAI's existing cache breakpoint. Saved-default/draft changes cannot alter it. No seed, runtime settings, paths or hashes are added to model input. |
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

Adding the public run-configuration prefix is a **v8 context fix**, not a new tool
interface: tools, IDs, observations and provider serialization are unchanged. The
snapshot identity changes for new runs. Restore, funded continuations and branches
inherit the original pair and prefix; a conflicting pair is refused for snapshots
that contain it, including human continuations. Legacy snapshots without the field
remain without it and are never backfilled from current settings. The exact deployed
#69 source has a [reviewed source-compatibility rule](restore-compatibility.md#frozen-run-configuration-upgrade)
requiring the ordinary explicit Restore acceptance. Start a new root run to receive
the added context; older runs keep their original model inputs.

The defaults below come from `config.py`; bytes and provider tokens are separate
units. Overrides are recorded and cannot silently widen a frozen episode.

| Setting or constant | Default |
| --- | ---: |
| `max_input_tokens_per_call` / `DEFAULT_INPUT_TOKEN_LIMIT` | 32,768 tokens |
| `max_request_bytes` / `DEFAULT_REQUEST_BYTE_LIMIT` | 262,144 bytes |
| `INPUT_TOKEN_SAFETY_MARGIN` | 512 tokens |
| `CONTEXT_FRAMING_BYTES` | 4,096 bytes |
| `HELPER_PAGE_BYTES` | 2,048 bytes |
| `RETAINED_HELPER_RESULTS` | 3 receipts per completed decision in working memory |
| `WORKING_MEMORY_DECISIONS` | 3 completed decisions |
| `WORKING_MEMORY_BYTES` | 24,576 bytes |
| `ALWAYS_LOADED_MAX_BYTES` | 1,024 bytes |
| `max_game_actions` | 1,500 |
| `max_provider_calls` | 2,000 |
| `max_helper_calls_per_decision` | 8 |
| `max_output_tokens_per_call` | 32,768 tokens |
| `max_transport_attempts` | 3 |

The output ceiling includes reasoning and the final tool call; it is not a generation
target. Higher ceilings increase the worst-case pre-call reservation, while complete
usage still settles at the reported charge. Explicit YAML/saved limits override this
default. Update saved operator limits separately for future runs; existing frozen runs
retain their recorded allowance, including 8,192-token runs.

Provider requests use buffered streaming. Both native runtimes assemble a complete
response before any operation is decoded or executed; a tool fragment is never an
action. The 90-second timeout bounds each stalled read, not total reasoning time.
Stop is checked between chunks and before calls/retries; a stalled connection can
still wait for its read timeout. Native game timeouts are unchanged. Injected
transport clients retain their own timeout settings.

A read timeout ends the current episode with `INFRASTRUCTURE_FAILURE` / `PROVIDER_READ_TIMEOUT`.
A broken response (`ReadError` or `RemoteProtocolError`, including a server disconnect)
ends it with `INFRASTRUCTURE_FAILURE` / `PROVIDER_RESPONSE_LOST`. In either case, usage
is unknown, so the failed request's single reservation stays retained; there is
**no in-episode retry**. Provider exception text is never copied into the journal.
Campaign scheduling is unchanged: infrastructure failures may get a separate episode
attempt, up to the existing two-attempt limit, under the shared ledger and campaign cap.
Only `ConnectError`, `ConnectTimeout` and `PoolTimeout` are retryable transport
exceptions, with the existing bounds and a separate reservation for each attempt.
`WriteError` and `WriteTimeout` can occur after request bytes were sent; their
reservation stays retained and they are not retried. Other transport exceptions stop as non-retryable
`PROVIDER_TRANSPORT_UNKNOWN`. Provider-specific billing errors are permanent even
when carried by HTTP 429; bounded transient retries honor Retry-After. Free token
counting retries do not consume generation allowance. Missing final usage, stream
errors and interrupted responses retain uncertain spending. No SDK retry or hidden
continuation request is permitted. Saved settings and historical limits stay intact.
An explicit HTTP 408 is retryable because it reports request timeout/rejection,
unlike an ambiguous client read or write timeout. HTTP 409 is not automatically
retried: an unclassified conflict needs diagnosis. Other transient statuses are
429, 500, 502, 503 and 504, plus Claude's 529; permanent billing/configuration codes
override this list. Both the header parser and the stop-aware runner cap delays
at 60 seconds.

Fit an unsent initial request by trimming the oldest working-memory frame, then
public events; never trim the current observation, notebook or action constraints.
After transmission, freeze the initial message and preserve every delivered result
and native continuation block unchanged. New tool results append a shared
`{result, context_update}` envelope containing only values changed since their last
delivery (the initial snapshot counts). Notebook state and permitted tools replace
their previous values; status, maintenance and remaining-budget objects merge by
field. Unchanged notebook text and guidance are not repeated. The model input omits
internal retrieval metadata and obsolete helper-eviction warnings. An as-of
provider-attempt counter accompanies every update. A transport retry reuses the exact
prepared request; the next distinct update includes all intervening attempts.
No-call feedback invents no tool ID; multiple calls receive error results and
execute nothing. Overflow fails with `LOCAL_CONTEXT_LIMIT` or `INPUT_TOKEN_LIMIT`,
without silent eviction, summarization or a wider allowance. The transcript ends
at the game action or actor handoff. Across-action working memory is unchanged.
Frozen `knowledge.json` retains rules/skills; `uv run bh guide package` regenerates the guide for future episodes.
`ActionEnvelope` pairs the current observation ID with one typed action and may carry a memory replacement
and decision note. Validate phase, handles, counts and order before dispatch. Never send raw RPC, seeds,
saves, endpoint text or private provider credentials to the model.

## Providers

OpenAI Responses and Anthropic Messages have separate native runtimes behind one
metered policy contract. Observations, local legality, helpers and spending stay shared.
OpenAI uses strict tool schemas; Claude uses the same canonical catalog non-strict
because its strict grammar has different bounds and aggregate limits. Both apply
the full local schema before decoding an operation. Pure catalog/settings preflight
runs before game creation. Automatic selection never forces an extra model call.
Schema feedback identifies missing and unexpected keys, including nullable note
fields and nested note objects. Complete, metered responses containing malformed or
duplicate-key tool JSON consume the same bounded invalid-response allowance for
both providers, rather than becoming transport failures. For Claude, only the bad
tool input is wrapped as `{"INVALID_JSON": raw_input}` to keep the replayed `tool_use`
object legal; its ID and opaque reasoning blocks remain unchanged, and the matching
`tool_result` reports the error with `is_error: true`. The private assembled response
marks the parse error so wrapped input can never execute; this marker is not sent.
This follows Claude's [invalid tool JSON error-return guidance](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming#handling-invalid-json-in-tool-responses);
fine-grained streaming is not enabled. Broken SSE/event envelopes, interrupted
streams and missing final usage still fail closed without executing a tool or
resending an uncertain generation. A capped live canary remains separately gated;
it should measure required-field omission and maximum inter-chunk silence by model/effort.
Provider-native reasoning/tool blocks continue within one decision, resetting after the action. Every request,
including retries, reserves configured ceilings/prices. Unknown usage stays reserved; record overage and stop
the next request at the applicable episode/campaign cap. Paid calls require operator enablement, settings,
and episode/batch caps. Anthropic `temperature` is incompatible with manual `thinking_budget`.
Current Claude presets use adaptive thinking instead; see [Claude setup](#claude).

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
outputs. OpenAI's `comparison_response_id` advances only after a completed response with
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

### Claude

In **Models & budgets**, choose a Claude preset, review its dated prices, then
**Save model**. This does not change existing models, paid-execution permission,
or spending caps. Configure `ANTHROPIC_API_KEY` in the backend using the credential
workflow above, not the browser. In **Start a run**, choose the saved Claude model
and reasoning effort. Launch choices are run-only unless explicitly saved as defaults.
An API key is required; a Claude chat subscription is not an API credential.

Organization-wide/multi-workspace keys also require `ANTHROPIC_WORKSPACE_ID` in
the backend environment. Both token counting and generation send it through the
same `anthropic-workspace-id` header builder. Omit it for a single-workspace key;
an absent or empty value sends no workspace header. It is not a model setting
and is never added to request bodies, frozen protocols or public run records.
See Anthropic's [authentication contract](https://platform.claude.com/docs/en/api/overview#authentication).

On hosts using the age-backed secret helpers, store both values with hidden
prompts (`addsecret ANTHROPIC_API_KEY` and `addsecret ANTHROPIC_WORKSPACE_ID`),
then add `ANTHROPIC_WORKSPACE_ID` to the existing `secretrun` launch list:
`secretrun OPENAI_API_KEY ANTHROPIC_API_KEY ANTHROPIC_WORKSPACE_ID -- bh review ...`.
Use the existing launch arguments and restart only when the worker is idle.
Do not put either value in source files, command arguments or the dashboard.
The offline-check command strips all three values before running checks.

The presets use the direct Messages API, standard global capacity
(`service_tier: standard_only`), adaptive thinking, and efforts `low`, `medium`,
`high`, `xhigh`, `max`. Fable and Sonnet default to `high`; Opus and Haiku to
`medium`. These exact model IDs reject `temperature` and manual `thinking_budget`
settings locally. Older/custom identifiers retain manual thinking, whose budget
must be smaller than the output ceiling; undeclared adaptive effort is refused.
The shared output ceiling stays 32,768 and the harness stays `tools_v8`.

Standard global API USD/million, verified 2026-10-08:

| Exact model | Input | Cache read | 5-minute cache write | Output |
| --- | ---: | ---: | ---: | ---: |
| `claude-fable-5-1` | 10.00 | 0.25 | 12.50 | 50.00 |
| `claude-opus-5-5` | 4.00 | 0.20 | 5.00 | 20.00 |
| `claude-sonnet-5-5` | 2.00 | 0.10 | 2.50 | 10.00 |
| `claude-haiku-5-5` | 0.10 | 0.01 | 0.125 | 0.50 |

Haiku's preset covers at most 100,000 total input tokens (including cached input).
Larger input ceilings refuse before counting or generation because long-context
pricing is unconfigured. All presets keep the existing 32,768 input ceiling.
The full worst-case reservation must fit the chosen dollar cap; for example,
Fable's default per-call reservation is $2.048. Saving a model never raises a cap.

Both cache prices enable a five-minute breakpoint on the frozen system prefix,
after tools and before dynamic observations, money, memory and helper outputs.
There is no warmup request or promised hit rate. Leaving both cache rates blank
keeps the legacy uncached request. Anthropic's reported `input_tokens` are ordinary
uncached input; reads and writes are separate, disjoint categories. Signed and
redacted thinking blocks continue unchanged inside a decision, and reasoning
tokens are already included in output. Malformed usage, inconsistent write
details, an unconfigured TTL/tier/region or missing write pricing retains the
full reservation. Input counting includes adaptive thinking and output effort.

Sources: [model IDs/defaults](https://platform.claude.com/docs/en/models/overview),
[pricing](https://platform.claude.com/docs/en/about-claude/pricing),
[effort](https://platform.claude.com/docs/en/build-with-claude/effort),
[caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
[Messages request](https://platform.claude.com/docs/en/api/messages/create),
[input counting](https://platform.claude.com/docs/en/api/messages/count_tokens).
Offline tests use mocked HTTP and synthetic games; they do not establish live
Anthropic compatibility or model quality. Native behavior is unchanged and uses
existing evidence. [Older-run compatibility](restore-compatibility.md#claude-provider-addition)
preserves eligible OpenAI runs without silently upgrading historical Claude runs.

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
