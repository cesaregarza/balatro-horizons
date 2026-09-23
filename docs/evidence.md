# Evidence

Native evidence is a bounded regression proof for one pinned Balatro runtime.
It does not establish model quality, optimal play, headless equivalence, or
support for untested decks, stakes, cards, bosses, or continuations. Calibration,
fixtures, and assisted branches are excluded from autonomous scores.

## Plan and stages

Inspect the non-executing plan first:

```bash
uv run bh evidence plan
uv run bh evidence plan --gameplay-only
uv run bh evidence plan --resume-actions
uv run bh evidence plan --resume-certification
uv run bh evidence plan --connection-only
```

The cold plan has 12 physical launches and 22 resets. Startup profile stability
uses two launches; functional collection reuses the retained process; fresh
process restoration uses nine launches; branch restoration uses one. Gameplay
only uses one process and eight resets and does not certify restoration.

| Stage | Launches / resets | Evidence artifact |
| --- | --- | --- |
| startup profile stability | 2 / 4 | `runtime-audit-{stake}.json` |
| functional collection | 0 / 8 | `native-fixtures-final.json` |
| fresh-process restoration certification | 9 / 9 | `certificate-record-*.json` |
| branch restoration | 1 / 1 | `native-release.json` |
| gameplay collection only (alternative plan) | 1 / 8 | `native-gameplay-collection-*.json` |

Collectors use `EvaluatorSession` ownership, public actions, fresh journals,
handle issuers, and request IDs. Ambiguous execution retires a session; unknown
status is infrastructure failure and is never silently retried. Resume only a
named stage with matching prerequisites, and identify the episode when resuming
an action fixture using `bh evidence collect --from-stage "resumed functional
collection" --episode-id EPISODE_ID`. A stage name selects the remaining suffix;
it never turns missing prerequisite artifacts into completed evidence.
The resume plans report 11 launches / 16 resets for actions, or 11 / 11 for
certification. Selecting the gameplay-only stage requires `--gameplay-only`.

For an authorized connection-only check, use `bh native diagnose --connection
--config configs/smoke.yaml --report reports/verification/native-connection-UNIQUE.json`.
Its plan is one calibration launch and zero game resets. It requires clean
committed source, a matching pinned environment manifest/instrumentation, an
explicit session registration, and an idle runtime/port. It temporarily removes
only this checkout's registration to prove fail-closed RPC admission, restores
and refreshes it, and opens a new RPC subprocess against the same owned game.
The temporary rename leaves concurrent registration readers unconfigured. An
abrupt process kill during that window can leave registration absent: after the
diagnostic has ended and its owned game is cleaned up, explicitly register again
with `bh review session --apply` from the connected terminal. Do not refresh the
registration while the diagnostic is still running or restart the backend to
bypass this fail-closed window.
Cleanup stops only that launch identity. It neither simulates actual socket
destruction nor claims replay, gameplay, or capability certification. Every
attempt gets a new immutable source/environment-bound receipt, including failures;
no backend deployment, provider call, or certificate activation is performed.
Main-menu connection readiness is distinct from Lua's settled-gameplay flag:
an identity-verified, non-busy `MENU` response suffices for this zero-reset check.
The receipt records phase and gameplay readiness separately, without asserting
that a game action would be legal. Busy responses still fail the bounded wait.

```bash
uv run bh evidence collect
uv run bh evidence certify
uv run bh evidence publish
```

## Certification and scope

Certification compares public-state and private-continuation fingerprints across
at least three fresh processes. Seed-prefix certificates cover only their
recorded prefix and suffix; direct-save support is per checkpoint. `publish`
validates required artifacts and the schema-selected public export before
activating a pointer to immutable certificate records. A divergence leaves a
private artifact and failed immutable record; an old pass is never rewritten.
The failure artifact is `divergence-*.json`. Inspect its boundary summary with
`bh evidence inspect PATH --limit 20`; raw private divergence values stay private.
`private/capability-certificate.json` selects the active immutable certificate.
Seed-prefix failure aborts certification; direct-checkpoint failure is recorded
in `native-release.json` while the passing seed-prefix capability remains usable.
This three-pass certification is release evidence and remains separate from
ordinary recovery: a branch replays its recorded prefix once, checks public and
private state along the replay and at its target, then continues in that same
game process before any provider call.
Admission readiness is not a replay pass: the worker must still reach the
saved boundary successfully before deciding anything new. Recovery requires
the checkpoint's exact frozen implementation and protocol; native evidence
reuse does not migrate an older run across a harness-source change.
The release collector's scripted branch is explicitly calibration-only and
still requires checkpoint replay evidence; it can bootstrap release evidence
without requiring the release certificate it is about to produce. That path
is not exposed by the ordinary branch API and cannot run a paid or human policy.

Every non-calibration launch needs a certificate matching source, environment,
deck, stake, injector, bridge, and full mod tree. Filename classification alone
cannot establish scope: the manifest's explicit runtime/source scope and
fingerprint must be checked. Absent evidence is named as skipped, never counted
as passing; a passed record naming a missing artifact fails as corrupt.
Headless and accelerated modes remain uncertified.

Explicit replay certification checks the registered Windows connection at admission
and again under the native lock before each repetition. A recognized session
configuration/expiry error aborts without writing a failed certificate or
changing the selected checkpoint certificate. It is not replay evidence.
Other mid-replay transport failures remain conservatively failed evidence and
can disable a prior pass for the same mode; do not interpret them as a gameplay
divergence or automatically resend the action. Refresh registration before an
explicit retry. Competing worker admissions return `WORKER_BUSY` while replay
holds the worker reservation. [Issue #42](https://github.com/cesaregarza/balatro-horizons/issues/42)
tracks evidence-based classification of ambiguous transport loss before any
change to that conservative certificate-selection policy.

### Bounded interruption study

With explicit native-path authorization, `bh evidence plan --interruption-only`
describes at most seven launches: one unpaid initial-blind fixture, three fresh
seed-prefix comparisons, and one owned process each for deliberate divergence,
a bounded game hang, and post-send transport loss. Run it with
`bh evidence collect --interruption-only --report reports/verification/native-interruption-UNIQUE.json`.
The immutable receipt binds clean committed source, pinned runtime, scenario
order, identity, and cleanup. Stop on the first unexpected failure; there is no
automatic retry or uncertain-action resend. All fixture and raw state remains
private or excluded evaluator evidence, with zero provider calls.

The divergence row perturbs actual native inventory before comparing the
unchanged expected continuation hash; it is not a spontaneous restoration bug.
The hang row verifies the owned executable, PID, nonce and start time, then uses
an eight-second debugger hold with detach-on-helper-exit behavior. Transport
loss terminates only the existing RPC subprocess after one action write/flush;
accepted stdin bytes alone do not prove the game received or committed it.
The adapter's single request-status query supplies the observed status.

| Observed class | Interpretation and existing ordinary selection policy |
| --- | --- |
| Actual state mismatch | Proven divergence; retain private evidence, failed ordinary recheck can disable its mode's pass. |
| Committed request and matching settled state | Reconciled connection interruption; never resend the action. |
| Unknown request, timeout, EOF, or hung game without comparison | Unknown game outcome, not proven divergence; ordinary replay remains conservatively fail-closed. |
| Explicit registered-session error before a repetition | Abort before the next launch or certificate write; preserve the selected pointer. |

This collector uses the existing comparison functions but writes **no certificate
records or pointers**. It does not change the ordinary or separate probe policy.
Actual WSL socket expiry between repetitions is not exercised by this slice:
`actual_socket_expiry_tested` and `complete_issue42_acceptance` remain false.
Mocked expiry tests establish ordering only, not native socket-loss evidence.
The partial study cannot close #42, authorize a live branch, or certify a release.

The remaining row has its own nonexecuting plan, `bh evidence plan
--session-expiry-only`, and command:

```sh
bh evidence collect --session-expiry-only --fixture-report reports/verification/native-interruption-PRIOR.json --report reports/verification/native-session-expiry-UNIQUE.json
```

It requires additional explicit authorization for the Windows `wsl.exe` path.
One disposable command in the same distro reports its own interop socket; a
shared operator socket is refused before any game launch. At most **one** fresh
replay reuses the prior excluded fixture, only when its native identity, runtime,
configuration, collector and replay oracle are unchanged. Historical source
hashes are retained alongside the new source and prior-receipt digest, never
rewritten or promoted to a certificate.
Both native receipts are mode `0600`, stored under gitignored
`reports/verification/`, and are not included in public exports or published.

After the owned game closes, stdin requests normal exit of only the disposable
command. The collector must observe actual socket disappearance and the real
`WINDOWS_SESSION_EXPIRED` preflight before repetition two, with zero subsequent
launches. No socket is removed and no distribution or WSL VM is stopped. A
surviving socket or unexpected error fails the study without retry. Registration
bytes are restored in `finally`, the operator socket identity must remain intact,
and journal/certificate content is checked for changes. If an unexpected session
expiry prevents normal game cleanup, the restored operator session may stop only
the retained owned nonce; this is cleanup, never replay or action retry.
Hard-killing the collector
can still interrupt restoration: use `bh review session --apply` from an active
Windows-connected terminal before further work; do not retry a native suite.

The disposable child has an independent 180-second input deadline, but the
collector's 20-second exit wait does not signal or kill it. On
`DISPOSABLE_CHILD_EXIT_TIMEOUT`, an orphaned Windows `wsl.exe` command may survive
the collector. Operator cleanup requires identifying that **specific** invocation
in Windows process inspection by its checkout, module, and unique `--child`
argument, then closing only that command; never terminate all `wsl.exe` processes,
the distro, or the WSL VM. Receipt child/relay PIDs are Linux identities, not
Windows process IDs. If the invocation cannot be identified unambiguously, stop
for operator investigation rather than guessing or retrying.

Microsoft
[WSL source](https://github.com/microsoft/WSL/blob/56244fdb65508a4628c38865f0e2278f779b81f4/src/linux/init/init.cpp#L2166-L2171)
resets the relay's interop server before exit, but actual disappearance is an
observation requirement, not an assumption about the installed Windows version.
Only the combined passed receipts cover #42's classification matrix. They still
do not grant restoration certification, a live branch, or a release, and do not
change either certificate-selection policy.

### Cost-stopped continuation probes

A terminal budget boundary may have no recorded replay suffix. Ordinary budget
continuation uses the same single-replay recovery path: it replays the recorded
prefix once, checks public and private state through the target, and continues
in that same game process before any provider call. `bh continue-budget
EPISODE_ID --plan` reports zero verification launches and one separately funded
continuation launch; it executes neither. The optional `--verify` diagnostic
can still run the separate `checkpoint_probe`, which compares the original
private continuation hash in at least three fresh processes, applies the same
validated public action each time, and compares the resulting hashes. Those
fresh processes provide diagnostic evidence and are not required before an
ordinary budget continuation.

Generated actions are private evaluator evidence, never parent history or a
claim about the parent's unobserved future. Probe records use a separate pointer
and cannot authorize ordinary branches. In particular, an evaluator-fixture
checkpoint or its probe result is not an ordinary production recovery prefix.
Replay certificates alone do not grant budget-extension spending authority. The probe
checks explicit session registration at admission and again before each locked
repetition. Its owner-requested policy
preserves the selected probe on operational errors, while proven private-state
or same-action divergence records failure and invalidates it, even if cleanup
also fails. It stops at the first divergence and has no automatic replay fallback.
If cleanup also fails during an operational error, the original error stays
primary; a sanitized cleanup-code exception note is retained and copied into the
collector receipt's `probe_cleanup_reasons`, without changing the selected certificate.
This differs deliberately from the conservative suffix-replay diagnostic policy above;
#42 must justify any future unification using native failure evidence.

The probe implementation participates in the native identity; its sequencing
cannot be changed under harness-only evidence reuse. Old source hashes and frozen
protocols are never rewritten to make an existing root executable on new code.
For a clean-source native regression without a paid root or running backend:

```bash
uv run bh evidence plan --continuation-only
uv run bh evidence collect --continuation-only --report reports/verification/native-continuation-UNIQUE.json
```

This bounded Red/White fixture uses at most four launches: one initial-blind
checkpoint capture, then three fresh-process `select_blind` probes. It stops at
the first failure and preserves an immutable, sanitized receipt, the original
parent journal, and private divergence evidence. It requires an idle runtime,
explicit Windows registration, and matching pinned instrumentation. No provider
is called, backend started, old protocol rewritten, or capability activated.
The evaluator-aborted fixture is not a cost-exhausted model run; a pass proves
only that checkpoint and action, not later phases or a paid continuation.
It does not override historical direct-save replay failure evidence.

The accepted native gate in [PR #25](https://github.com/cesaregarza/balatro-horizons/pull/25)
covers that source-bound fixture only, not changed native implementations or a
paid root. Further native execution remains operator-gated; offline doubles
demonstrate control flow only, not Windows restoration fidelity.

## Acceptance map

These are bounded regression contracts, not claims about every card, boss, mod,
or stochastic interaction. Unsupported availability fails closed.

| IDs | Contract and evidence home |
| --- | --- |
| AT-01–04 | Public allowlists, privacy scans, concealed-handle eviction, resources, prices, capacities, and ordering: boundary/state tests plus native concealed-state fixtures. |
| AT-05 | All 14 strategic action families: blind select/skip; play/discard/reorder; buy/sell/use; shop/boss reroll; pack choose/skip; cashout/leave, covered by native action fixtures. |
| AT-06–09 | Persisted intent, validation/no-mutation rejection, permutation fidelity, and known-commit replay versus unknown-status failure: runner, action, dispatch, reorder, and transport tests. |
| AT-10–12 | Fresh-process continuation, same-action replay, and terminal win/loss distinction: restoration certificates, seed-prefix replay, and excluded evaluator fixtures. |
| AT-13–15 | Torn-tail recovery, cost/limit durability, and provider-contract parity: storage, spending, and mocked provider suites; live compatibility stays separately authorized. |
| AT-16–18 | Progressive exposure, append-only annotation revisions, immutable parents, and explicit assistance: review API/browser and branch tests. |
| AT-19–20 | Attempt accounting and seed-clustered uncertainty: reporting and deterministic bootstrap tests. |
| AT-21–24 | Schema-selected exports, synthetic provenance, isolated runtime identity, and rejection of uncertified/assisted evidence: export, native-gate, and release tests. |

## Reuse and publication

Harness-only candidates may reuse evidence only with an immutable parent
certificate, unchanged `contracts.py`, `game/` (except `fake.py`), `observations/`,
`actions/`, and `storage/` bytes, the same environment lock, existing
artifacts, and a matching offline report. Reuse records the original native
identity, candidate identity, parent certificate, and zero native launches; it
does not migrate checkpoint certificates or frozen protocol snapshots.
The receipt includes the per-file native manifest as well as the aggregate hash.

From the candidate checkout, first create its source-bound offline report, then
use the baseline checkout holding the immutable certificate and native artifacts.
Omit `--apply` to inspect the refusal or reuse plan:

```bash
uv run bh offline --report reports/verification/offline.json
uv run bh evidence reuse \
  --root ../baseline-checkout \
  --candidate . \
  --baseline auto \
  --offline-report reports/verification/offline.json \
  --apply
```

The command refuses changed native files, environment drift, missing artifacts,
or a report bound to a different candidate. A success records zero native
launches and never activates broader restoration scope.
`--baseline auto` finds the committed revision matching the accepted source hash;
`CERTIFIED_BASELINE_REVISION_NOT_FOUND` means that revision is absent from local
history. Recover the matching history or choose a verified explicit baseline;
do not rewrite the certificate hash.

Public exports are schema-selected and privacy-scanned. Seeds, raw state, saves,
credentials, private paths, and divergence payloads stay private. Red/White and
Red/Gold are the supported certified configurations. Paid-provider compatibility
and scientific evaluation require separate evidence and budgets; this pipeline
makes no paid calls.
