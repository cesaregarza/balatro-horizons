# Current harness

## What the model receives

The single current harness sends a frozen prompt, a rules kernel, stable tool
definitions, and a dynamic public `Context` on each provider call. The context
contains the current compressed observation and action constraints, current
cost quotes, the permitted tool names and helper allowance, a run notebook,
bounded completed-decision frames, the latest observed action outcome, and
delivery/omission metadata. OpenAI and Anthropic use the same builder and
renderer; frozen episodes keep their recorded prompt and tool bytes. No hidden
card identity, future draw, provider-private reasoning, or automatic strategy
summary enters this context.

The prompt incorporates [`ALWAYS-LOADED.md`](../configs/prompts/ALWAYS-LOADED.md),
a 1,024-byte mechanics reference. Edit that source and run
`uv run python scripts/sync_prompt_instructions.py --write`; without `--write`,
the script checks freshness. Stale or malformed content blocks a new run.
The generated block in `harness.txt` is not an editing target. Configured
notebook-key and retained-helper limits are rendered into the prompt from
`config.py`; the synced mechanics block is guarded separately.

`working_memory` keeps up to `WORKING_MEMORY_DECISIONS` completed decisions,
bounded by `WORKING_MEMORY_BYTES`. Each frame carries exact public pre-action
resources, committed action, model-authored decision note, following public
result, and up to `RETAINED_HELPER_RESULTS` helper receipts. Oversized helper
receipts are omitted whole; oldest frames leave first. Historical handles and
quotes are informational—only the current observation authorizes an action.
The latest `previous_action_outcome` pairs a verified public delta with the
prior model claim and linked note edit when matching journal events exist;
missing or pruned evidence stays null. The model, not the harness, reconciles
its notes with the observed outcome.

## Providers

Both direct providers use `harness/transport/base.py` for request admission,
HTTP delivery, response classification, the six-stage tool-call parser,
continuation capture, and conservative usage settlement. `openai.py` and
`anthropic.py` supply immutable specs rather than alternate state machines.
Each spec declares its terminal field and mapping, call item type, identifier
and argument fields, result item type, and cache options.

Provider modules also own their supported settings and model capabilities.
OpenAI declares cache-diagnostic eligibility, explicit-cache eligibility,
unsupported setting values, display names, and verified reasoning efforts;
Anthropic declares thinking-budget support. `ModelConfig` validates through
those tables, and `/api/bootstrap` publishes the same declarations to the
workbench. The browser does not infer compatibility from model names. Unknown
models and missing declarations fail closed for cache-dependent launches.

OpenAI uses Responses with encrypted reasoning continuations, explicit prompt
cache accounting, and disjoint ordinary/read/write input categories. Anthropic
uses Messages with native thinking blocks and conservatively retains the full
reservation when cache-write usage cannot be priced. Every provider retains the
reservation when usage is missing or malformed. Paid execution remains disabled
by default; credentials stay in the backend environment and are never serialized
into run configuration.

Tests inject `httpx.MockTransport` into `Transport`; this HTTP seam covers real
request bodies, status handling, parsing, continuation, and accounting without
patching transport internals or making provider calls. New runs save a canonical
provider/model key and freeze exact settings and prices. Historical aliases and
recorded runs remain readable, while the workbench deduplicates aliases for new
selection. The smoke preset remains `configs/luna-smoke.yaml`; retired interfaces
and historical live-check results are archived in the changelog and verification
artifacts rather than maintained as a second harness guide.

## Context limits

The notebook has model-chosen keys and a total character bound. Gameplay tools
may attach one nullable `note_update` without a helper call; separate set/delete
helpers remain available. Action and edit are both validated before execution.
A valid edit is journaled before the native action and can survive a subsequent
native failure without claiming action success. `notebook_maintenance` warns
about likely frame or helper-result displacement, not guaranteed retention.

The complete request obeys `max_request_bytes` (default 262,144), including
framing/settings headroom. If it is too large, the builder removes oldest
working-memory frames, then current-decision helper results, then oldest recent
public events; it never trims the current observation, notebook, or action
constraints. Failure is `LOCAL_CONTEXT_LIMIT`. Independently, provider input
counting enforces `max_input_tokens_per_call` (default 32,768) with a 512-token
safety margin; unavailable counts fail closed. Spending reserves the configured
input/output ceilings, not an estimate derived from request bytes.

The source journal and complete delivered requests remain authoritative.
Branches inherit only public ancestry through their boundary observation;
later parent events cannot enter the child. Dynamic notebook and history do
not change the fixed cached prefix. Offline tests cover both transports,
byte-bound precedence, branches, note/action failure isolation, and frozen
delivery. They establish interface behavior, not better gameplay or native
fidelity. Executable harness changes alter the implementation fingerprint;
check [native evidence reuse](evidence.md) before activation.
