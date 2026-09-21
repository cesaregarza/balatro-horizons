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

## Reuse and publication

Harness-only candidates may reuse evidence only with an immutable parent
certificate, unchanged native `game/` bytes, the same environment lock, existing
artifacts, and a matching offline report. Reuse records the original native
identity, candidate identity, parent certificate, and zero native launches; it
does not migrate checkpoint certificates or frozen protocol snapshots.

Public exports are schema-selected and privacy-scanned. Seeds, raw state, saves,
credentials, private paths, and divergence payloads stay private. Red/White and
Red/Gold are the supported certified configurations. Paid-provider compatibility
and scientific evaluation require separate evidence and budgets; this pipeline
makes no paid calls.
