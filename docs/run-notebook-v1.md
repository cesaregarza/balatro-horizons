# Run notebook and action-result retrieval (`tools_v6`)

V6 lets the model keep notes between decisions and retrieve a committed gameplay
action directly, including its public before/after conditions. It retains v5's
provider continuation within a decision, fixed tool catalog, dynamic current
costs, knowledge access, and conservative spending accounting. The new interface
is recorded in the frozen protocol. Historical runs and their journals are not
migrated.

## Notebook contract

`set_run_note(key, text)` creates or replaces one entry; `delete_run_note(key)`
removes one entry. Both are optional helpers and leave the game unchanged.
Keys are chosen by the agent, contain 1–64 Unicode characters, and cannot be blank
or contain ASCII control characters. The existing `memory_max_characters`
allowance applies to the sum of key and text lengths, measured in Unicode code
points; the default is 4,096. Fixed notebook metadata does not count against that
allowance, but the complete serialized notebook counts against request size and
provider input limits.

There are no prescribed note categories or automatic economic/strategic advice.
The prompt identifies the notebook as agent-authored, possibly mistaken or stale;
current public observations remain authoritative. Gameplay tool schemas no longer
contain `memory_update`, and v6 rejects that legacy argument. V5 continues to use
its recorded whole-string memory policy.

An accepted edit is appended as a `run_note` event before it becomes visible or is
acknowledged. Each mutation records its predecessor revision, new revision,
operation, key, optional replacement text, and resulting content hash. Oversized
edits return a structured error and leave all notes unchanged. Invalid edits still
consume the helper attempt. The current entries, revision, content hash, and
character usage and limit appear in `run_notebook` on every model request,
including the immediately following helper call. Clearing an older helper result
never clears the notebook or suggests repeating a mutation to reload it.

The notebook lives in the dynamic user message, after the fixed instructions and
tools. Note changes and helper availability do not change the OpenAI cache
breakpoint. Provider-native continuation blocks remain within the decision and
are reset after the gameplay action, as in v5. This preserves cache structure;
it does not establish a measured reduction in cost or better play.

## Action evidence

`last_action` remains automatic. `retrieve_action_result` provides direct access
to the latest committed gameplay action (`decision_id: null`) or a specified
decision. `episode_id` disambiguates inherited decisions; null means no episode
filter. Helpers, note writes, rejected requests, and aborts do not become the
latest gameplay action.

The tool accepts `section: receipt`, `before`, or `after`, with `byte_offset: 0`
for the first UTF-8 page. Follow `next_offset` until `complete` is true. All three
sections carry references to the action event and available observation events,
including episode IDs. Missing or not-yet-committed decisions return the same
unavailable result; there is no future-outcome lookup.

The receipt separates `recorded_decision_note` from `observed_result` (the recorded
public delta). The note is not assumed to be a prediction. Full public conditions
are retrieved through the before/after sections instead of repeating them on every
request. Historical handles cannot be used as current action targets. No engine
state, seed, hidden draw order, or provider-private continuation is read by this
tool. A missing score breakdown remains unknown: `hand_score` and
`scoring_breakdown` are null, and a public chip-counter change is never promoted to
an authoritative per-hand score.

## Helper exhaustion

The existing configured per-decision helper allowance covers reads, arithmetic,
and note edits. It resets after a committed gameplay action. At zero, the dynamic
`permitted_tools` contains only current gameplay tools and `abort_run`, and the
request explains that helpers are exhausted. The fixed catalog stays unchanged.

An attempted extra helper gets linked feedback and consumes the bounded invalid
response allowance. It does not immediately produce `BUDGET_EXHAUSTED`, execute
an alternative move, or grant more helpers. Repeated invalid requests still end
as `AGENT_PROTOCOL_FAILURE`; provider-call and monetary ceilings remain enforced.
Legacy interfaces retain their earlier helper-limit termination semantics.

## Checkpoints, review, and exports

Every v6 checkpoint contains the **pre-decision** notebook. Restoration folds the
hash-checked journal prefix and requires an exact snapshot match. Branches inherit
only events before their specified boundary, recursively through immutable parent
references. Later parent notes and edits made while deciding the overridden move
are excluded. A failed acknowledgment after a durable write does not erase the
event, but does not move an earlier checkpoint's boundary either. Mid-decision
crash resumption is not introduced.

Prospective review does not expose the decision's note edits before action reveal.
Public bundle exports include `run_note` revisions through the existing privacy
scanner; opaque provider continuation remains excluded. The current public
observation schema remains unchanged, and v6 does not populate its legacy memory
field. The complete delivered request is still journaled for reconstruction.

## Validation and rollout

Offline tests cover atomic size rejection, Unicode accounting, immediate delivery,
helper pruning, durable writes and acknowledgment failures, bounded exhaustion,
both provider transports, stable cached prefixes, exact action receipts,
temporal cutoffs, nested branch inheritance, snapshot tampering, frozen prompt
restoration, and prospective/export boundaries. Synthetic mechanics and mocked
providers are application evidence only.

The 2026-09-17 combined offline gate passed: **394 Python tests, 18 browser tests,
Ruff, diff checks, TypeScript, and the production build**. Nine native tests were
skipped. Python 3.12.10 and uv 0.8.17 match the CI pins; Node 22.12.0 matches the
repository pin. Checks ran in an isolated Linux worktree with a separate browser
test backend on port 8766, zero native launches, and zero paid provider requests.

Use `scripts/check_offline.py --web` with locked dependencies and Node 22.12.0 for
the combined offline gate. `scripts/sync_prompt_instructions.py` checks v6 by
default; `--write` updates it explicitly, and `--interface tools_v5` selects the
legacy template without rewriting any frozen episode protocol.

Runner and agent changes alter the native implementation fingerprint even though
the Lua adapter is unchanged. For each deployment, verify the dedicated runtime
with one announced consolidated suite, then deploy backend and frontend together
while the worker is idle. Preserve operator budgets, model settings, and the
existing helper allowance. Do not start a paid smoke run without explicit
authorization and caps.

The deployment helper supports `--prepare --allow-unrelated-changes` to retain
local edits outside the candidate's file set. It rejects overlapping edits and
untracked collisions. `--snapshot-only` checks the manifest and captures Linux
source, settings, evidence, and frontend backups without installing files, so a
subsequent Git fast-forward can keep the deployment on its actual committed head.
Seven focused deployment-helper tests passed after this addition.

## Local deployment record

V6 was deployed on **2026-09-17** from harness commit `59971ed` and deployment-helper
commit `da747ea`. The service was stopped while idle, matching rollback files were
saved, and the local checkout was fast-forwarded without changing its earlier
uncommitted helper-limit and UX work. Runtime instrumentation required no reinstall.

One consolidated unpaid native suite passed, with its planned **12 process
launches**: two startup/profile checks, six seed-prefix repetitions across two
traces, three direct-checkpoint repetitions, and one branch restoration. Ordinary
gameplay cases shared a process. There were no failed checks or suite retries.

| Evidence | Result |
| --- | --- |
| Red/White and Red/Gold profile stability | Passed |
| Action coverage, ordered objects, packs, invalid-action isolation | Passed |
| Native terminal detection and acknowledgment faults | Passed |
| Action-fixture seed replay (`61bd7bef5a5e45e68ca40910736ecea0`) | Three repetitions passed |
| Gold baseline replay (`9769a11c8d47424485dafeedc3b68825`) | Three repetitions passed |
| Direct checkpoint at Gold decision 0 | Three repetitions passed |
| Assisted branch (`865422fbed754193a6376569ceb91a84`) | Completed; original journal unchanged |
| Settlement evidence | Passed; no extra launches |
| Activated capability, doctor, native evidence gate tests | Passed; zero blockers; 10 tests passed |
| Deployed browser review | Passed prospective withholding, reveal, native hand rendering and branch comparison; zero browser errors |

The native suite checks the runtime/shared execution path; notebook and provider
transport behavior are covered by the separate synthetic and mocked-provider
tests above. V6 has **no paid-provider validation or measured gameplay/cost
improvement** yet. Calibration fixtures and assisted branches are not autonomous
benchmark scores. Source and environment identities are:

```text
implementation: 0250a3940507b28312724d93ce001b48b77fae7b1930944134f70cff2f8e840f
environment:    908ff9f9db75e4abe92d963e4a17620dda65393bfd78a6725f0e89538027a631
```

Local artifacts remain outside Git under `reports/verification/` and
`private/deployments/notebook-da747ea/`. The native capability covers the recorded
certified boundaries; it does not certify every possible checkpoint in a phase.
The backend and v6 frontend are live together. The existing 24-helper allowance,
$5 episode ceiling, $10 total ceiling, and model settings were preserved. This
deployment made zero paid provider requests and left the worker idle.
