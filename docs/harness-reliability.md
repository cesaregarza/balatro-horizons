# Harness reliability changes

Implemented from merged `main` (`0d0872a`) in an isolated Linux worktree. Native
verification passed on 2026-09-16 before deployment. Direct provider compatibility
remains a separate, unverified gate.

## Input admission and diagnostics

- Transport: `max_request_bytes=262144`, independent of the token/cost ceiling.
- Tokens: full provider input counting, with 512 tokens of headroom. Unsupported
  counting requests fail closed. The headroom is an operational cushion, not a
  mathematical guarantee about provider estimates.
- Spending: existing conservative maximum-input-price reservations and all-attempt
  accounting remain in place. Counting completes before reserving for generation.
- Exact provider items and call IDs remain within a decision, including when an
  old helper result is replaced by a reload receipt. Context overflow never drops
  an opaque continuation to make a request fit.
- Typed failures publish only fixed codes, stages and numeric measurements.
  Unexpected exceptions retain their type publicly. Private diagnostic files
  contain stack locations, excluding exception text, source lines and locals.
- New runs reject stale/malformed `ALWAYS-LOADED.md` embedding. Existing episodes
  and ordinary branches retain their frozen instructions. Its mechanics prose
  and the gameplay objective have not changed.
- Manual Anthropic thinking rejects simultaneous temperature configuration.
  This does not certify every model's thinking mode or other provider settings.

Counting sources: [OpenAI token counting](https://developers.openai.com/api/docs/guides/token-counting),
[OpenAI spending-controller example](https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api),
[Anthropic token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting).
Generation-only fields are omitted from counting requests; input items and tool
schemas are passed unchanged. No network fallback estimates tokens from JSON bytes.

## Native settlement visibility

The project-owned Lua hook observes the pinned engine's computed cash-out rows.
It never calls scoring, bonus, tag or RNG functions to reconstruct a payout. It
preserves the original function calls and returns, and captures the bottom-row
total, the first seven displayed rows, and the native omitted-row count.

The inspected native implementation omits the interest row for balances below
$5 or disabled interest; it does not always display an explicit `$0` row. The
public observation preserves that omission. Rules on displayed interest and
remaining-hand rows use the native localization and current public parameters.
No hidden overflow row, card pointer, internal config, or native save field enters
the public settlement schema. Settlement is shown only in `ROUND_EVAL`, after the
bottom row is computed, and only for the current round. Historical traces with
no captured settlement remain unknown; they are never rewritten.

## Descriptive metrics, version `public-economy-v1`

Both transcript audit scripts include the same measurement implementation:

- One settlement per committed `cash_out`, with balance before collection,
  displayed total, recorded interest and its observation basis.
- An absent interest row in a complete, untruncated breakdown is a zero payout.
  Missing breakdowns and unavailable interest behind omitted rows remain unknown.
- One balance per committed `leave_shop`; rejected proposals do not count.
- Played rounds with at least one negative balance observed in `SELECTING_HAND`,
  deduplicated by ante, round number and blind. This measures sampled debt, not
  the number of elapsed frames or unobserved time in debt.
- Score/target ratio at cash-out when both numeric values are known.

These are retrospective descriptive measurements, without an economy score,
savings target, purchase ranking, or automatic horizon judgment. They do not
change prospective-review response boundaries. Bootstrap reports additionally
flag insufficient seed clusters and degenerate between-seed variation; the
existing percentile interval is retained and a collapsed interval is not certainty.

## Validation and activation

Initial offline gate: **312 passed, 9 native gates skipped**; Ruff and Git whitespace
checks passed. Prompt freshness and added-line credential scans passed. The test
environment uses the locked dependencies and Python 3.12.10. Two existing
FastAPI/Starlette dependency deprecation warnings remain. No Balatro process was
launched during those offline checks and no paid generation request was made.

Tests cover both providers' large helper continuations, original item ordering
and call IDs, invalid/excessive counts without generation, byte/cost independence,
instruction freshness and frozen branches, safe diagnostics, Lua cash-out hooks,
hidden row exclusion, public projection and descriptive metric missingness.
All tests deny real HTTP by default; mocked transports remain usable. One older
probe test initially missed the new counting route and attempted a request using
a dummy test key. It failed before generation; the route is now mocked and real
HTTP is blocked throughout the suite.

The saved Terra decision 48 reconstructs with all provider items preserved under
the separate transport ceiling. Reapplying the old 32768-byte bound produces a
specific helper-follow-up overflow, including retained-item counts. The helper
reconstruction tool reads hash-checked evidence and emits only measurements;
it neither launches the game nor counts tokens through a live API.

The new GitHub Actions workflow runs locked Python 3.12.10 dependencies and the
same offline script. Hosted CI has not run until the candidate is published.

## Native verification, 2026-09-16

One consolidated unpaid suite passed without a repeated collection or a runtime
fix. It used 22 game launches across collection, three-pass replay and direct-save
proofs, and branch verification. A separate web-worker startup check is part of
deployment. These are calibration and assisted diagnostics, excluded from model
performance scores.

The final local CI check passed **324 tests with no skips**, including the nine
evidence gates that previously lacked native artifacts. Ruff and Git whitespace
checks passed; the same two dependency deprecation warnings remain.

- Red/White and Red/Gold profiles matched across two fresh processes each.
- Full action coverage, invalid-action isolation, native win/loss detection,
  ordering, duplicate requests, and both acknowledgment-loss outcomes passed.
- Three complete action-fixture replays certified recorded blind-selection,
  cash-out, shop and multi-choice-pack boundaries. Three Gold replays additionally
  certified the recorded hand-selection boundaries.
- Direct saves passed three fresh-process checks at the Gold run's initial
  blind-selection checkpoint only. Other boundaries retain their separate
  seed-prefix proof; arbitrary direct-save restoration is not certified.
- A separate override branch finished with its parent journal unchanged.
- `verify_settlement_evidence.py` checked three committed cash-outs: two with an
  interest row and one with no interest row. Public rows matched the native
  capture, displayed totals matched balance increases, and settlements were
  absent in 66 observations from other phases. Hidden overflow-row exclusion is
  covered by the Lua unit test, not a newly claimed native overflow fixture.

The release collector now runs this settlement check, and activation refuses a
missing or stale settlement artifact. The check reads existing hash-checked
journals and snapshots, requires matching implementation/environment provenance,
and launches no additional games. Regression tests reject changed public rows
and incorrect post-cash-out balances.

Native implementation fingerprint:
`1e1d4636586644bc240465e37214568a5a5cb5ee2adf241446c7578a4465a3fd`.
Runtime environment fingerprint:
`03ffd235312449ae6c2a74d9d33093d3fd0f6b4691ab13c34371fe9f93166dd3`.
Private local evidence lives in `reports/verification/native-release.json`,
`native-settlement.json`, and `native-evidence.json`; the active gate is
`private/capability-certificate.json`.

No paid provider calls were made. Live counting with real encrypted continuations,
live Anthropic compatibility, headless equivalence and model-performance improvement
remain unverified. Native verification does not establish any of those properties.
