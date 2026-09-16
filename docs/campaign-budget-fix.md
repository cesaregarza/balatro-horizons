# Campaign funding and episode budgets

This implements Part A of the [September 16 implementation handoff](implementation-handoff-2026-09-16.md).
The baseline was the clean public checkout at
`f85b3483da5c59a930307b01a3f2dc244e12439c`. Work is isolated on
`fix/campaign-budget`; the running workbench was not updated.

## Corrected behavior

Previously, campaign and episode refusals both raised `COST_CAP_REACHED` and
became valid `BUDGET_EXHAUSTED` outcomes. A batch could construct an unfunded
episode, record a zero-call loss, and continue to later slots. The first new
integration test reproduced this on the original production modules: two valid
attempts and zero unresolved slots, instead of one valid attempt and one unresolved
slot.

| Refusal | Episode result | Batch behavior |
| --- | --- | --- |
| Next paid slot cannot fund its initial reservation | No episode or game constructed | Record a preflight `CAMPAIGN_COST_CAP` stop |
| Episode cap only | `BUDGET_EXHAUSTED` / `EPISODE_COST_CAP`; valid non-win | May schedule later slots |
| Campaign cap only during an episode | `CAMPAIGN_INTERRUPTED` / `CAMPAIGN_COST_CAP`; unresolved | Stop scheduling |
| Both independently bind | `BUDGET_EXHAUSTED` / `EPISODE_AND_CAMPAIGN_COST_CAP`; valid non-win | Stop scheduling |
| Missing/invalid paid caps or disabled authorization | Configuration/authorization error | Reject before episode/game creation |
| Episode cap below one reservation | `EPISODE_CAP_BELOW_RESERVATION` configuration error | Reject before checking campaign affordability |

Action, provider-call, and helper limits keep their previous classifications.
Scripted baselines do not require paid configuration. Credential checks remain
required for paid models. Validation does not instantiate a provider client.

## Admission and durable evidence

The runner, batch preflight, and Luna smoke preflight use one unchanged
worst-case reservation formula. It uses the actual upcoming model, including
maximum input pricing when cache-write rates are higher. The admission predicate
is always `committed + required <= cap`, using the same ledger sum and numeric
representation. Subtracted headroom is diagnostic only, including negative
headroom for recorded overages. There is no epsilon or credit refund.

Preflight reads a locked, consistent snapshot and is **non-binding**. Every
provider request and transport retry still needs an authoritative reservation
under the spending lock. Unknown usage retains its full reservation. The existing
one infrastructure replacement per slot is preserved.

Each frozen batch has a serialized scheduler and a first-write-only `stop.json`.
The record includes its schema, batch, recorded time, reason, stage, slot, agent,
and public cost context. In-episode stops also identify the episode, outcome, and
terminal event's ID, hash, sequence, and original timestamp. The stop write is
atomic under a lock, followed by directory fsync. Later writes preserve the first
record's bytes.

Before scheduling, the service validates the original configuration hash and
evidence kind, then reconciles stops with original episode manifests and
hash-checked journals. A terminal committed before the stop write is recovered
even when SQLite has no episode row or still has a null summary. A recovered
record has `recovered: true`; its `recorded_at` is distinct from the terminal's
original timestamp. Unreadable or invalid stop records fail closed. Assisted
children never resolve slots or stop original batch scheduling.

Reports and actual `public.json` exports use authoritative original attempts and
include the same `scheduling_stop` field (`null` when absent). Campaign-interrupted
eligible attempts remain in all-attempt costs and outcome counts but not valid
outcomes. Planned denominators, first-valid selection, seed/replicate matching,
and uncertainty calculations are unchanged. The existing export privacy scan is
unchanged and is exercised by the export integration tests.

## Validation

All new accounting numbers are frozen synthetic fixture values, not current API
prices or native gameplay results. The tests use the real Store, Runner,
RunService, report/export functions, and provider request/parse code with mocked
HTTP and `FakeGame`. A patched native constructor also proves unfunded native
dispatch stops before game construction.

The acceptance suite covers T01–T12, retained transport reservations,
model-specific affordability, exact and adjacent float boundaries, invalid
configuration precedence, fresh-service restarts, terminal/index/stop crash
windows, concurrent stop writers, corrupt stops, legacy records, assisted-child
exclusion, real exports, and unchanged non-cost limits. The smoke preflight has
separate exact-boundary and configuration-error tests. A provenance test confirms
changes to the new scheduling module invalidate the native fingerprint.

Final offline validation on September 16, 2026, with Python 3.12.10:

```bash
uv sync --locked --offline
uv run --offline --no-sync pytest -q tests/test_campaign_budget.py
uv run --offline --no-sync pytest -q
uv run --offline --no-sync ruff check src tests scripts
git diff --check
```

- Locked dependency synchronization succeeded from the local cache.
- Campaign-budget acceptance tests: **49 passed**.
- Full Python suite: **214 passed, 9 skipped**, in 20.54 seconds.
- Ruff and whitespace checks passed.
- Eight skips require native evidence; one requires the absent pinned BalatroBot
  source checkout. None represents a passed native gate.
- Two dependency deprecation warnings concern Starlette's HTTPX integration and
  the AnyIO `BlockingPortal` import alias. They are unrelated to this patch.
- No frontend code changed, so frontend tests/builds were not rerun.

## Limits and next gate

No paid-provider calls, Windows runtime launches, native validation, or deployment
were performed for this patch. Offline tests establish accounting and scheduling
behavior; they establish neither native fidelity nor improved gameplay.

Budget, runner, service, contract, and scheduling source are included in the
implementation fingerprint. Existing native certificates cannot authorize this
changed implementation. Before native use, inspect and run the existing release
verification procedure with explicit native-access authorization; the handoff
identifies `verify_release.py --resume-certification` as the prior entry point.
No certificate hashes or enforcement checks were bypassed.

There is no funding amendment/resume protocol. Changing a frozen cap still raises
`BATCH_CONFIGURATION_CHANGED`. Legacy `COST_CAP_REACHED` journals are neither
rewritten nor reclassified. Part B's information, context, protocol, and UI work
remains a separate backlog. No frontend consumer required changes for this patch.
