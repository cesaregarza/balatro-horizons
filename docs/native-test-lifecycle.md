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

The consolidation was first staged in a separate Linux checkout. Offline tests cover
launch counts, factory sharing, cross-stake startup grouping, ownership cleanup,
nonce checks before mutation, fault-wrapper isolation, session retirement, and
the Lua profile/counter reset. Native reset equivalence was left pending at that
stage; the deployment below supplies evidence for the tested cases. Subsequent
native patches require installation, a regenerated environment lock and fresh
matching evidence; these certificates do not validate arbitrary future changes.

Future native verification should execute the selected plan once. If a case
fails, retain its evidence, fix the relevant issue, and repeat only the necessary
checks. Announce startup/restoration/recovery launches; do not add invisible
retries or restart the whole application for an ordinary new game.

Before a native patch deployment, the existing installer can snapshot the owned
instrumentation to a new private Linux directory:

```bash
uv run python scripts/install_candidate.py --root . \
  --snapshot-runtime --backup private/deployments/RELEASE/native
```

This requires the operator's Windows-path authorization. It copies pinned mod
files, the bridge, injector and runtime lock, checks their bytes, and refuses an
existing backup destination. It excludes credentials, saves, checkpoints and
generated Lovely logs/dumps. The ordinary Linux install backup also preserves
the backend environment lock and frozen rules. Neither backup operation launches
a game. The backup helper's five focused tests pass with Linux-only fixtures.

## Deployed validation — 2026-09-16

The consolidated suite completed its twelve planned native launches. Startup and
profile stability, shared-process action coverage, terminal detection, ordinary
Red/White and Red/Gold outcomes, ordering, and acknowledgment faults passed. Both
seed-prefix proofs passed three fresh-process repetitions, as did direct restore
at the tested Gold checkpoint. The assisted branch completed without changing its
parent. Headless and accelerated execution remain uncertified.

The final recorded-settlement check then exposed an existing coverage dependency:
both ordinary baselines lost before cash-out, leaving no zero-interest sample.
The ordering fixture now keeps its starting cash below $5 and uses its injected
Jokers to clear the first blind, instead of applying the $100 easy-blind setup.
One targeted startup verified that change. No engine or mod changed, and no
completed restoration proof was repeated. Total deployment launches were **13**;
future full suites retain the **12-launch** plan because the corrected case is
inside the existing shared process.

The final settlement check verified both positive-interest and omitted-interest
rows. The public cost checker verified 58 contexts and 11 reconstructed receipts.
Capability activation and all ten native evidence gates passed. Both `bh doctor`
configurations reported no blockers. There were **zero paid calls**, and operator
spending settings remained byte-identical to the pre-deployment snapshot.

The validated executable implementation fingerprint is
`91636d162331ff6404127d0d98ad11f7d1eb1e533e55ed85cea25a3f0151b3e1`;
the native environment fingerprint is
`908ff9f9db75e4abe92d963e4a17620dda65393bfd78a6725f0e89538027a631`.
Local evidence is retained under `reports/verification/`. Rollback snapshots and
both the original suite log and targeted-fix log are under
`private/deployments/consolidation-75bcfbe/`. Calibration and assisted episodes
remain excluded from autonomous model scores.
