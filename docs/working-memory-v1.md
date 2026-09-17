# Working context and notebook maintenance (`tools_v7`)

V7 preserves recent public working context across gameplay actions and instructs
the model to maintain its own concise run notebook when its conclusions or plan
change. It adds a one-edit notebook attachment to gameplay tools, avoiding an
extra provider round trip when the agent is already ready to act. V6 and older
interfaces retain their prompts, schemas and context behavior.

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

## Notebook guidance and clearing notice

The fixed prompt instructs the agent to maintain its plan, useful conclusions,
and unresolved questions; update changed notes, remove obsolete entries, and
avoid duplicating the current board. It reminds the agent to preserve useful
conclusions before their supporting context disappears.

The dynamic `notebook_maintenance` block identifies the oldest frame that will
leave after the next action when the decision count is full. It also signals
when the next helper may displace a currently loaded helper result. Byte pressure
can clear context earlier; the retention limits and omission counts remain
explicit. This is an advance reminder, not a guarantee against every eviction,
and it does not add an automatic model call or force a note on every turn.

## Editing a note alongside an action

All v7 gameplay tools have a nullable `note_update` field:

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
V6 and older interfaces reject the new argument.

## Provenance, branches, and caches

Every v7 checkpoint includes pre-decision working context and notebook state.
Branch admission folds the immutable public ancestry, applies the checkpoint's
boundary observation, and requires an exact working-context snapshot match.
Later parent helpers, actions and notes cannot enter a child branch. Reused
decision numbers remain disambiguated by episode ID. Current-decision edits
remain behind prospective action reveal, as in v6.

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
attachments, both provider transports, fixed prefixes, legacy interfaces,
invalid-action isolation, storage/native failure handling, nested branches,
snapshot tampering, and prospective/export boundaries. All games in these tests
are deterministic fixtures; no paid-provider or native result is implied.

The 2026-09-17 offline gate passed: **418 Python tests, 18 browser tests, Ruff,
diff checks, TypeScript, and the production build**. Nine native gates were
skipped. Python 3.12.10 and Node 22.12.0 were used in an isolated Linux worktree;
the browser suite used its fixture backend on port 8766. There were zero native
launches and zero paid provider calls. The full API/browser suites required host
local-socket access after the sandboxed API tests stalled; no assertion failed.

The source selector targets v7. Deploy the backend and frontend together only
when the worker is idle. This change does not alter the native interface or
runtime: a fresh offline report and an explicit comparison with the certified
source can accept the new harness using the existing native evidence, with zero
Balatro launches. See [certification scope](native-certification-scope.md).
Old checkpoint certificates retain their exact full harness identity and are
not migrated. Paid behavior checks remain a separate gate.

Design references: [Codex instructions](https://developers.openai.com/codex/guides/agents-md),
[OpenCode V2 compaction](https://opencode.ai/v2/docs/compaction), and
[Hermes persistent memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory).
V7 adapts their separation of durable guidance, recent working context, and
editable memory; it does not reproduce their cross-session learning systems.
