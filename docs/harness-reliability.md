# Harness reliability changes

Prepared from merged `main` (`0d0872a`) in an isolated Linux worktree. The serving
checkout and its running process are unchanged. Native activation and direct
provider compatibility remain unverified for this candidate.

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

Final offline gate: **312 passed, 9 native gates skipped**; Ruff and Git whitespace
checks passed. Prompt freshness and added-line credential scans passed. The test
environment uses the locked dependencies and Python 3.12.10. Two existing
FastAPI/Starlette dependency deprecation warnings remain. No Balatro process was
launched and no paid generation request was made for this candidate.

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
Native verification must check settlement values and row visibility through the
whole path, preserve action transitions, and recertify the new implementation and
runtime. Live counting with real encrypted continuations remains a separate gate.
No native fidelity, checkpoint expansion, headless equivalence, or model-performance
improvement is claimed from these offline tests.
