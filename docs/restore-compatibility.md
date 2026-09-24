# Older-run source compatibility

Restore resolves each saved checkpoint/protocol source hash to immutable Git
source, verifies the full historical fingerprint, and compares it with the
current executor. Historical code is parsed as data, never imported or executed.
Missing history, unmatched behavior changes, retired interfaces or missing frozen
snapshots refuse admission. The current native release/environment gate and one
checked replay still apply; a source comparison is not a native replay pass.

## Identity coverage

Native game, observation, action, storage and contract files plus
`evidence/continuation_probe.py` are compared byte-for-byte. For synthetic
parents, `game/fake.py` is also compared byte-for-byte. It is excluded only for
native parents because that simulator does not execute native games.

Every other Python file covered by the full implementation fingerprint is
compared by whole-module AST, including all harness/provider/context code,
configuration, **every** service method, branch/budget/restore orchestration,
spending ledgers and evidence code (recovery, provenance, lock, certification
and collectors). No service entry points or method bodies are dropped.
AST comparison ignores comments and formatting; docstrings and executable
statements remain significant. Files outside the full implementation fingerprint,
such as the web/API presentation layer, are not an execution-compatibility claim.

The only fingerprinted Python exclusions from both comparison layers are:

- `evidence/compatibility.py`: the current receipt admission and validation policy.
- `evidence/execution_identity.py`: the current comparison algorithm and selectors.
- `evidence/identity_migrations.py`: the current reviewed migration catalogue.

Those three define compatibility itself, rather than historical gameplay. Their
exact bytes are still pinned by the receipt's full **current** implementation
hash, and changing any of them invalidates existing receipts. They require code
review, not a claim of equivalence with their historical versions.

## Explicit historical migrations

The catalogue admits exact, named **whole-module AST** versions reviewed from
`5ad4219` (pre-single-restore), `2d31ce0` (single-restore) and `75d384e`
(initial Restore). It translates only historical hashes to pinned current hashes.
The current side is never normalized: reverting production release gating, or
changing any accepted module, is not automatically compatible. An arbitrary edit
inside a recognized old module does not match its catalogue entry.

The named migrations are:

- `service.py`, `service_execution.py`, `workbench/branches.py`,
  `workbench/budget_continuation.py`, `evidence/certification.py` and
  `evidence/release.py`: #57's single checked recovery, bounded ancestry replay,
  explicit scripted release calibration, and production gating of resumed work.
- `harness/context/freeze.py`, `harness/decision.py`, `harness/runtime.py`:
  exact frozen-protocol compatibility guards, current executor binding,
  continuation-hash requirement and child receipt persistence.
- `evidence/provenance.py`: fingerprint the newly introduced Restore policy.
- `evidence/recovery.py`: accept a separately validated source receipt and bind
  it to the checkpoint's game kind.
- `harness/terminals.py`, `workbench/budget_ledger.py`,
  `workbench/restore_ledger.py`, `workbench/restoration.py`: shared terminal
  outcomes and ledger row validation, corrected lost-game refusal, game-kind
  binding and public historical commit provenance.

For the exact full-source fingerprints of the first two releases only, the
catalogue permits the missing new Restore modules (`service_restore.py`,
`workbench/restoration.py`, `workbench/restore_ledger.py`), and the missing
#57 `evidence/recovery.py` on the first release. Their current AST hashes are
pinned too. Missing files on arbitrary historical revisions are not allowed.
These are explicit recovery-policy upgrades, not assertions that old and new
admission behavior is identical. Other unchanged historical source can pass
without a catalogue entry when all included identities already match.

## Receipt, branch scope and trust

The private receipt binds historical Git commits, original protocol hash, game
kind, native/execution identities and the exact current implementation. It is
revalidated at execution and carried into child checkpoints. Public restoration
metadata exposes only the historical commit-ID list in `source_revisions`,
not source paths, seeds, frozen configuration or private proof content.

Original protocol bytes keep their old source hash; the decision loop rejects
current source changing during execution. An ordinary branch directly from an
old-source root still fails `CHECKPOINT_IMPLEMENTATION_CHANGED`. A branch from
an admitted Restore child inherits its immutable proof and may continue under
the same validated compatibility contract; it does not rewrite the old root.

The proof assumes the local frozen protocol/configuration and checkpoint records
are trustworthy. Hash binding detects inconsistent or later changes; it does
**not** prove that historical code actually produced the original configuration.
An operator who forges internally consistent private records is outside this
trust boundary. Historical configuration is never validated by executing old code.

Compatible-update acceptance and paid execution authorization remain separate.
No parent record, checkpoint/release certificate, environment gate, model,
prompt, knowledge or paid cap is rewritten.
