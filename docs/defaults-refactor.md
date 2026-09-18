# Shared harness defaults refactor

Prepared on 2026-09-16 from `44d4a4a` in a native Linux worktree on branch
`refactor/harness-defaults`.

## Change

`src/balatro_horizons/config.py` now owns the shared context, paging, history,
retention, memory/note, arithmetic, instruction-size and provider-timeout settings,
alongside its existing episode-budget and environment defaults. Consumers import
the shared values; `focused.PAGE_BYTES` and `focused.RETAINED_RESULTS` remain
compatibility aliases. Tool schemas, runtime validation and frozen memory-policy
metadata retain the same values.

The six YAML presets contain their actual overrides rather than repeating Python
defaults. Model identifiers, prices, explicit smoke/probe cost caps and probe
limits are preserved. Existing operator settings and run journals are untouched.
The README documents precedence and compares this project's research emphasis
with the primary sources for BALROG, Evalatro and BalatroBench.

## Offline verification

- Full Python suite: **295 passed, 9 native gates skipped**.
- Ruff, Git whitespace check, and persistent-prompt freshness check: passed.
- Canonical serialization hashes for all six resolved preset configurations
  matched the original checkout before edits: no resolved configuration change.
- No gameplay prompt content, provider schema values, budget values, or native
  Lua patches changed. The known byte/token coupling remains unfixed.

The suite used the existing locked Python environment with the candidate source
selected explicitly through `PYTHONPATH`; provider credentials were removed.
Two dependency deprecation warnings were emitted for the FastAPI/Starlette test
client. No Windows runtime launch, paid call, service restart or new native
certificate was performed for this refactor.

## Activation boundary

Executable source changes invalidate the native implementation fingerprint even
when they preserve behavior. This candidate is **not activated in the running
workbench**. Activation requires an idle worker and matching native verification;
the existing release certificates must not be edited or reused as evidence for
the candidate. Prefer consolidating deployment with the pending context-limit
fix to avoid an unnecessary separate native verification cycle.
