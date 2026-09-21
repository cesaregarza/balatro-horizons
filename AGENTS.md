# Balatro Horizons local rules

- Keep all game seeds, raw engine state, native saves, credentials, and private manifests outside the public trace and exports.
- Offline fake episodes are test evidence only. Never report them as native Balatro results.
- Do not make paid provider calls without explicit operator authorization and episode and batch cost caps.
- Do not claim checkpoint fidelity from a save call alone. Disable live branches until the pinned native environment has a passing phase-specific certificate.
- Preserve original run journals and annotation revisions; use append-only records.

- Ordinary native regression cases must reuse an owned game process and start a
  new game with menu/start. Relaunch only for startup/isolation, restoration, or
  explicit crash-recovery checks. Use the suite's `--plan` output to report the
  launch count and reasons before execution. Prefer `--gameplay-only` for a
  gameplay-only check; it does not produce restoration or release certification.
- Do not repeat a passing native check without a relevant change or unresolved
  failure. Stop and diagnose failures before any targeted retry; never silently
  rerun the entire suite or replace a failed same-process reset with a relaunch.
- Harness-only changes (prompts, context, notebooks, provider handling) do not
  require repeating native certification when the native interface and runtime
  are unchanged. Run relevant offline checks and explicitly reuse the existing
  evidence with `bh evidence reuse`; see `docs/evidence.md`. Do not rewrite
  historical source hashes or migrate checkpoint certificates. Changes to native execution sequencing
  still require relevant native checks, even if made in a harness file.
- Before changing research measures or annotation/navigation semantics, read `docs/research/reconciliation-2026-09-14.md` and its preserved source handoff; keep user decisions distinct from assistant proposals.
