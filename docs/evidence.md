# Evidence and native certification

Native evidence is a bounded regression proof for one pinned Balatro runtime. It
does not establish model quality, optimal play, headless equivalence, or support
for untested decks, stakes, cards, bosses, and continuations. Calibration,
fixtures, and assisted branches are excluded from autonomous performance scores.

## Plan before execution

Always inspect the non-executing plan first:

```bash
uv run bh evidence plan
uv run bh evidence plan --gameplay-only
```

Planning reads no private manifest or seed, launches no process, and contacts no
provider. Native collection requires the operator's separately granted runtime
access. Never add invisible retries: retain failures and resume from a named
stage only when its prerequisites still match.

| Stage | Launches | What it proves | Artifact |
| --- | ---: | --- | --- |
| Startup profile stability | 2 | Pinned identity and stable White/Gold profiles in fresh processes | `runtime-audit-{stake}.json` |
| Functional collection | 0 | Invalid actions, all strategic actions, terminal detection, ordinary runs, reorder and fault handling in the retained process | `native-fixtures-final.json` |
| Fresh-process restoration | 9 | Three fresh-process seed-prefix continuation probes for two episodes and three direct-checkpoint probes | `certificate-record-*.json` |
| Branch restoration | 1 | One immutable-parent assisted continuation | `native-release.json` |

The cold plan is 12 physical launches and 22 game resets. Gameplay-only uses one
process and eight resets; it writes collection evidence but neither certifies
restoration nor activates capabilities. Use `bh evidence collect --from-stage
NAME` to resume the declarative suffix; the command does not infer success from
old terminal output. A resumed action fixture requires its explicit
`--episode-id`; the journal and pinned environment must still validate.

## Collection and certification

Collectors use `EvaluatorSession`/`NativeSession` ownership and public game
actions. A game reset gets a fresh journal, handle issuer, and request IDs while
the caller-owned process remains pinned. Ambiguous execution retires the session;
unknown action status is an infrastructure failure and is never retried.

```bash
uv run bh evidence collect
uv run bh evidence certify
uv run bh evidence publish
```

Certification compares the public observation hash and private continuation hash
across at least three fresh processes. Seed-prefix certificates cover only the
recorded prefix and suffix they replayed; direct-save support is per checkpoint,
not universal. A divergence leaves a private `divergence-*.json` artifact and a
failed immutable record. Use `bh evidence inspect ARTIFACT` for its bounded diff.

`publish` validates every required artifact and the schema-selected public export
before it writes the active `private/capability-certificate.json`. The active file
is a mutable pointer to immutable certificate records. A failed check disables
only the selected restoration mode; it never rewrites an older passing record.

Every non-calibration native launch requires a passing certificate whose source,
environment digest, deck, and stake match. The environment lock pins the licensed
runtime, injector, bridge, and full mod tree. Source changes during collection or
verification fail closed. Missing empirical artifacts are reported as skipped,
with the missing artifact named; they are never counted as passing evidence.

## Evidence reuse

Harness-only candidates may reuse native evidence only through explicit review:

```bash
uv run python scripts/check_offline.py --report private/offline-candidate.json
uv run bh evidence reuse \
  --root /path/to/live --candidate /path/to/candidate \
  --baseline auto --offline-report private/offline-candidate.json
```

Preview is read-only. `--baseline auto` searches Git history for the exact
certified implementation identity and fails with
`CERTIFIED_BASELINE_REVISION_NOT_FOUND` when none exists. Reuse requires the
immutable parent certificate, unchanged native `game/` bytes, the same environment
lock, existing evidence artifacts, and an offline report bound to the exact
candidate. Apply only after the candidate is installed and the worker is idle.

The acceptance record preserves the original native-tested implementation hash,
adds the reviewed candidate hash and parent certificate, and records zero native
launches. It never migrates checkpoint certificates or frozen protocol snapshots;
those continue to require their full original implementation identity.

## Scope and operating limits

- Public exports are schema-selected and scanned; seeds, raw states, saves,
  credentials, private paths, and divergence payloads remain private.
- Red/White and Red/Gold are the currently supported certified configurations.
- Visible speed-1 execution is the tested mode. Headless and acceleration remain
  uncertified.
- Paid-provider compatibility, scientific evaluation, and model-quality claims
  require their own evidence and budgets; this pipeline performs no paid calls.
- Tests that need live artifacts skip with an artifact-specific reason on a clean
  clone, and release summaries count those checks as skipped rather than passed.
