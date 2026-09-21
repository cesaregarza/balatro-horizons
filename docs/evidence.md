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
```

The cold plan has 12 physical launches and 22 resets. Startup profile stability
uses two launches; functional collection reuses the retained process; fresh
process restoration uses nine launches; branch restoration uses one. Gameplay
only uses one process and eight resets and does not certify restoration.

Collectors use `EvaluatorSession` ownership, public actions, fresh journals,
handle issuers, and request IDs. Ambiguous execution retires a session; unknown
status is infrastructure failure and is never silently retried. Resume only a
named stage with matching prerequisites, and identify the episode when resuming
an action fixture.

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

Every non-calibration launch needs a certificate matching source, environment,
deck, stake, injector, bridge, and full mod tree. Filename classification alone
cannot establish scope: the manifest's explicit runtime/source scope and
fingerprint must be checked. Missing artifacts are named as skipped, never
counted as passing evidence. Headless and accelerated modes remain uncertified.

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
certificate, unchanged native `game/` bytes, the same environment lock, existing
artifacts, and a matching offline report. Reuse records the original native
identity, candidate identity, parent certificate, and zero native launches; it
does not migrate checkpoint certificates or frozen protocol snapshots.

From the candidate checkout, point to an immutable baseline and the candidate's
source-bound offline report; omit `--apply` to inspect the refusal or reuse plan:

```bash
uv run bh evidence reuse \
  --root ../baseline-checkout \
  --candidate . \
  --offline-report reports/verification/offline.json \
  --apply
```

The command refuses changed native files, environment drift, missing artifacts,
or a report bound to a different candidate. A success records zero native
launches and never activates broader restoration scope.

Public exports are schema-selected and privacy-scanned. Seeds, raw state, saves,
credentials, private paths, and divergence payloads stay private. Red/White and
Red/Gold are the supported certified configurations. Paid-provider compatibility
and scientific evaluation require separate evidence and budgets; this pipeline
makes no paid calls.
