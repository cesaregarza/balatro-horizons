# Native test process lifecycle

Ordinary gameplay cases start new games inside an owned Balatro process. They do
not close and reopen the application between cases. Physical launches are
reserved for startup/isolation, restoration, and explicit crash recovery.

## Select the necessary scope

Inspect the plan before running native commands:

```bash
uv run python scripts/verify_release.py --gameplay-only --plan
uv run python scripts/verify_release.py --plan
```

These plan commands do not open Windows files, load seeds, launch a game, or call
a provider. They describe expected execution, not completed native evidence.

| Scope | Expected game starts | Expected physical launches | Purpose |
| --- | ---: | ---: | --- |
| Gameplay only | 8 | 1 | Startup followed by all functional cases in one process |
| Full cold certification | 22 | 12 | Two startup processes plus ten restoration launches |

The eight functional games cover invalid actions, action coverage, win detection,
ordinary Red/White and Red/Gold runs, ordering, known acknowledgment loss, and
unknown action status. Unknown status runs last and retires the shared process.
It remains an infrastructure failure, and its ambiguous action is never retried.

Full certification checks both stakes in each of two fresh startup processes,
then reuses the second process for the functional collection. Fresh-process
restoration remains separate: three action-fixture seed replays, three ordinary
Gold seed replays, three direct checkpoint restorations, and one branch
restoration. These are the only additional launches in a successful full suite.

The gameplay-only path writes collection results. It does not certify restoration
or activate a native capability certificate. Choose full certification only when
those proofs are needed; do not treat every gameplay regression as a cold release.
The existing resume options retain their source/environment and completed-journal
checks. A plan explains their selected launch groups too.

## Ownership and reset boundaries

`NativeSession` owns one calibration process. A game leases it, verifies the
original process identity before menu/start, and uses its own RPC channel. Closing
the game closes that channel and releases the lease. Closing the session stops
only its owned process. Two games cannot lease it simultaneously. Failure or
ambiguous execution retires the session; there is no automatic relaunch fallback.
Paid runs retain their existing separate process ownership.

Each game has a fresh issuer, request IDs, journal and run state. Fault-injection
wrappers belong to that game's RPC channel. The native request ledger remains
intact, preserving duplicate-request behavior rather than clearing unresolved
execution to make a test pass.

The cached pinned game source shows that menu/start replaces `G.GAME`, but native
card IDs (`G.sort_id`) and in-memory profile statistics survive. A calibration-only
start hook therefore snapshots the initialized profile and ID counters before the
first scripted start, then restores that baseline before later starts at the
menu. It does not run for paid episodes, introduce a gameplay operation, clear
request history, or alter an in-progress run. Reset failures stop verification.

## Validation boundary

Offline integration: **373 passed, 9 skipped** on 2026-09-16. Ruff and whitespace
checks passed. Full, gameplay-only, and both resume plans were exercised without
native inputs. This task launched Balatro zero times and left the live workbench
unchanged.

The consolidation is staged in a separate Linux checkout. Offline tests cover
launch counts, factory sharing, cross-stake startup grouping, ownership cleanup,
nonce checks before mutation, fault-wrapper isolation, session retirement, and
the Lua profile/counter reset. Native reset equivalence has not been verified.
The new native patch must be installed and its environment lock regenerated before
collecting fresh matching evidence. Existing certificates do not validate this
new source or instrumentation.

Future native verification should execute the selected plan once. If a case
fails, retain its evidence, fix the relevant issue, and repeat only the necessary
checks. Announce startup/restoration/recovery launches; do not add invisible
retries or restart the whole application for an ordinary new game.
