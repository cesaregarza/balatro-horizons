# Balatro Horizons local rules

- Keep all game seeds, raw engine state, native saves, credentials, and private manifests outside the public trace and exports.
- Offline fake episodes are test evidence only. Never report them as native Balatro results.
- Do not make paid provider calls without explicit operator authorization and episode and batch cost caps.
- Do not claim checkpoint fidelity from a save call alone. Disable live branches until the pinned native environment has a passing phase-specific certificate.
- Preserve original run journals and annotation revisions; use append-only records.

## Documentation and change rules

- Do not add dated Markdown files under `docs/`; put historical decisions in the
  newest-first `CHANGELOG.md`.
- Every new script needs a `bh` subcommand home. Code exercised through
  `importlib` or `runpy` belongs in `src/` and is tested there.
- Keep one harness interface. Do not add a second `harness_interface` or version
  switch; protocol changes are commits with frozen identities.
- Do not add an export, feature flag, helper, or seam without naming its
  consuming surface in the same pull request.
- Every pull request names deletions and reports net lines changed.
- Write for a human reader: keep functions to one screen and comment decisions,
  not mechanics.

- Ordinary native regression cases must reuse an owned game process and start a
  new game with menu/start. Relaunch only for startup/isolation, restoration, or
  explicit crash-recovery checks. Use `bh evidence plan` to report the
  launch count and reasons before execution. Prefer
  `bh evidence collect --gameplay-only` for a gameplay-only check; it does not
  produce restoration or release certification.
- Do not repeat a passing native check without a relevant change or unresolved
  failure. Stop and diagnose failures before any targeted retry; never silently
  rerun the entire suite or replace a failed same-process reset with a relaunch.
- Harness-only changes (prompts, context, notebooks, provider handling) do not
  require repeating native certification when the native interface and runtime
  are unchanged. Run relevant offline checks and explicitly reuse the existing
  evidence with `bh evidence reuse`; see `docs/evidence.md`. Do not rewrite
  historical source hashes or migrate checkpoint certificates. Changes to native
  execution sequencing still require relevant native checks, even in a harness file.
- Before changing research measures or annotation/navigation semantics, read `docs/research/reconciliation-2026-09-14.md` and its preserved source handoff; keep user decisions distinct from assistant proposals.
