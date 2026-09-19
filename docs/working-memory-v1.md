# Working context, notebook maintenance, and observed action outcomes

The current single harness preserves recent public working context across gameplay actions and instructs
the model to maintain its own concise run notebook when its conclusions or plan
change. It adds a one-edit notebook attachment to gameplay tools, avoiding an
extra provider round trip when the agent is already ready to act. Retired
interfaces are archived in the [changelog](../CHANGELOG.md).

This is a harness condition, not a scoring intervention. There are no prescribed
strategic categories, economic recommendations, automatic strategic summaries,
cross-run memories, or penalties for leaving notes unchanged. More note writes
are not a benchmark outcome. Win rates, retrieval counts and all-attempt costs
would need measured comparisons before claiming better play or lower spending.

## Automatically retained context

`working_memory` contains at most three completed decisions, in chronological
order. Each frame includes its originating episode and decision, phase, progress,
pre-action resources, committed action, recorded decision note, and the following
public observation's `last_action` result. Up to three recent helper receipts from
that decision accompany it. These are exact public records, not generated
interpretations; the note is the model's account, not a verified prediction.

The limits live in `src/balatro_horizons/config.py`: `WORKING_MEMORY_DECISIONS`
(3), `WORKING_MEMORY_BYTES` (24,576 serialized UTF-8 bytes for the frame array),
`WORKING_MEMORY_HELPER_BYTES` (4,096 per helper record), and
`RETAINED_HELPER_RESULTS` (3). Oversized helper records are omitted whole and
counted. Old frames leave as a whole when the count or byte bound is exceeded;
an oversized newest frame can therefore leave no frames retained. No JSON page
is silently cut or presented as complete. Omission counts accompany the view.

The existing full request bound includes this context. If necessary, older
frames are removed before current-decision helper results; request-specific
omissions are recorded. Current observations, notebook entries and action
constraints are never removed to make history fit. Provider token ceilings and
spending reservations still apply separately.

Historical handles, prices and counters are explicitly labelled as historical.
Only current observation handles and quotes authorize actions. Past actions and
their conditions remain retrievable by episode-qualified decision reference.
Calling `inspect_state` again reads the current state, not the old inspection.
There is no replay of provider-private reasoning, signatures, or executable tool
messages across game actions. Within-decision provider continuation is unchanged.

## Explicit previous-action outcome

The outcome-feedback revision adds `previous_action_outcome` to the dynamic harness
request for both providers. It highlights the latest observed action's phase and
cash before/after, with blind status, displayed chip totals, the prior target,
and remaining hands/discards for a played hand. Existing transaction receipts
preserve their distinction between quotes, balance changes, and unmeasured actual
charges. This is a deterministic presentation of already-public evidence.

The prior decision note is copied alongside the outcome only when the retained
frame matches the decision, action, and complete public delta. It is labelled a
pre-action model claim. Missing or trimmed notes stay null; an absent or mismatched
last-action boundary produces no summary. Counter resets never become a fabricated
hand score or scoring breakdown. The full `last_action` evidence stays available.

The fixed prompt asks the agent to compare its expectation with the observed
outcome and correct contradicted notebook entries. Action-attached notes should
state intentions or predictions until success has actually been observed. The
harness does not grade the move, classify the model's prose, supply strategy, or
rewrite the notebook. No additional model call or helper operation is required.

The receipt is assembled before request-history pruning and remains available
when older frames are removed. It counts against the ordinary request bound.
The frozen memory policy records the outcome and working-memory versions; prompt
and source hashes distinguish each condition from older runs. Rollout
requires an idle worker and explicit reuse of unchanged native evidence.

### Linking action-attached notebook edits

`previous-action-outcome-v2` also includes `recorded_note_update` when the last
action carried a saved notebook edit. It preserves the original key, text (null
for a deletion), notebook revision, and journal references, with explicit
`before_action_execution` timing. The same record is retained in the bounded
`working-memory-v2` frame. Both providers receive it in dynamic context beside
the observed result; tool schemas and the instruction prefix remain static.

Linking requires three matching events in the same episode and decision: the
submitted action with its edit, the saved notebook mutation, and that exact
committed action. Outcome pairing additionally checks the following public
observation. Proposals without saves, standalone note helpers, rejected actions,
execution failures, and mismatched records cannot produce a linked edit. A saved
edit can survive failure without claiming that a game action succeeded.

The edit is historical, not the current value of its key: a later helper may
already have revised or deleted that entry. The prompt asks the model to compare
the pre-action claim with the observed outcome and reconcile its own notebook.
No automatic note rewrite, extra model call, or scoring judgment occurs. A
missing or pruned edit stays null; after request-history pruning, the already
assembled latest outcome and edit remain within the ordinary context bound.
Old checkpoint identities remain unchanged and are not migrated to v2.

Offline tests cover pre-action timing, set/delete edits, standalone helpers,
invalid actions, execution failure, later notebook rewrites, branch inheritance,
provider parity, pruning, outcome isolation, fixed provider prefixes, and
target-selection preservation. These tests use synthetic or recorded public
data; they do not establish better play.

## Notebook guidance and clearing notice

The fixed prompt instructs the agent to maintain its plan, useful conclusions,
and unresolved questions; update changed notes, remove obsolete entries, and
avoid duplicating current cash, prices, counters, or the board. It asks the agent
to keep evidence-backed corrections separate from its current plan, using another
key when useful, and mark untested explanations as hypotheses. It reminds the
agent to preserve useful conclusions before their supporting context disappears.
Keys remain model-chosen; no `learned/build/economy/threats` template is imposed.

The fixed tool descriptions and prompt also clarify target semantics:
`target_ids` selects cards without moving them. Position-dependent effects use
the physical hand-array order; Death converts the left selected card into the
right selected card. The model may reorder first when legal and then use the new
observation. The harness never rearranges targets or repairs an action for it.
This adds static guidance only, preserving caching and native action semantics.

The dynamic `notebook_maintenance` block identifies the oldest frame that will
leave after the next action when the decision count is full. It also signals
when the next helper may displace a currently loaded helper result. Byte pressure
can clear context earlier; the retention limits and omission counts remain
explicit. This is an advance reminder, not a guarantee against every eviction,
and it does not add an automatic model call or force a note on every turn.

## Editing a note alongside an action

All current gameplay tools have a nullable `note_update` field:

```json
{"key": "plan", "text": "Agent-authored conclusion to retain."}
```

The object sets/replaces one entry. `{"key":"plan","text":null}` deletes an
entry; `note_update:null` leaves the notebook unchanged. The existing Unicode
key and total notebook character limits apply. The separate `set_run_note` and
`delete_run_note` helpers remain available for multiple edits before acting.

Both the gameplay action and attached edit must pass validation before either
executes. A bad action cannot change notes; a bad edit cannot advance the game.
A valid edit is appended as the existing `run_note` journal event, then made
visible, before the native action executes. It remains durable if native
execution subsequently fails, without implying a successful game transition.
An append failure prevents native execution. Game actions remain exactly-once;
no action or note is silently repaired.

Attached edits consume no extra helper allowance or provider call. Their tokens
still cost money normally. Standalone note helpers keep their usual accounting.

## Provenance, branches, and caches

Every current-harness checkpoint includes pre-decision working context and notebook state.
Branch admission folds the immutable public ancestry, applies the checkpoint's
boundary observation, and requires an exact working-context snapshot match.
Later parent helpers, actions and notes cannot enter a child branch. Reused
decision numbers remain disambiguated by episode ID. Current-decision edits
remain behind prospective action reveal.

The source journal and complete delivered requests remain authoritative; history
is folded incrementally during a run, without scanning the journal anew on every
decision. Opaque provider turns and evaluator/private events never enter the
working-context fold. All existing public export scanning remains in force.

The interface, maintenance policy, tool definitions and retention limits are
recorded in the frozen protocol. Notebook content, recent frames and maintenance
status live in the dynamic message after the fixed prefix. Updating them does
not change tool schemas or cached instructions. This preserves the cache layout;
no live cache-hit or cost improvement is claimed.

## Verification and rollout

Offline verification covers bounded retention, exact results, helpers and note
attachments, both provider transports, fixed prefixes, invalid-action isolation,
storage/engine failure handling, nested branches, snapshot tampering, and
prospective/export boundaries. All game transitions in these tests use
deterministic fixtures; no paid-provider or native result is implied.

Deploy backend and frontend together only when the worker is idle. This slice
does not alter the native interface or runtime, but executable harness changes
alter the implementation fingerprint. Evidence reuse must be checked explicitly
against the certified native components before activation. See
[certification scope](native-certification-scope.md). Old checkpoint
certificates are not migrated. Paid behavior checks remain a separate gate.

Design references: [Codex instructions](https://developers.openai.com/codex/guides/agents-md),
[OpenCode V2 compaction](https://opencode.ai/v2/docs/compaction), and
[Hermes persistent memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory).
The harness adapts their separation of durable guidance, recent working context, and
editable memory; it does not reproduce their cross-session learning systems.
