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

Every non-calibration launch needs a certificate matching source, environment,
deck, stake, injector, bridge, and full mod tree. Filename classification alone
cannot establish scope: the manifest's explicit runtime/source scope and
fingerprint must be checked. Absent evidence is named as skipped, never counted
as passing; a passed record naming a missing artifact fails as corrupt.
Headless and accelerated modes remain uncertified.

Native replay checks the explicitly registered Windows connection at admission
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
