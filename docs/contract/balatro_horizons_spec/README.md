# Balatro Horizons — implementation handoff

This package contains a proposed specification, not a built or validated benchmark.

- `BALATRO_HORIZONS_SPEC.md` — the full implementation contract, protocol, schemas, milestones and 24 acceptance tests.
- `AGENT_HANDOFF.md` — the short assignment to give a coding agent along with the full specification.
- `pilot.example.yaml` — the proposed pilot configuration extracted from the specification; placeholders and environment locks must be resolved before use. Paid calls are disabled.

Attach the first two files to a coding-agent task, or place the package in its working repository and direct it to `AGENT_HANDOFF.md`. The example configuration is a starting point for the implementation, not a claim that the described CLI exists.

The proposed first release uses complete native Balatro runs, an information-safe structured interface, inspectable traces, expert annotations and certified checkpoint branches. Synthetic strategy tasks and a leaderboard are deferred.

Default configurations: Red Deck/White Stake for smoke tests, Yellow Deck/Gold Stake for the provisional strategic pilot. A pilot contains 20 development seeds × 2 replicates × 2 configured models = 80 planned model episodes, subject to explicit authorization and configured caps. These defaults are not a power calculation or an expected cost estimate.

Prepared 2026-09-14. Public upstream references are included in the specification; no live-game compatibility test has been performed for this package.
