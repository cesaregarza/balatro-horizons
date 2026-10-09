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

### Explicit funding-control upgrade

The exact deployed 16× source `8807caf56828351c8a0ca12b5a59b2c944a4b7aa`
has a separate, full-source-gated funding migration. It pins old and new ASTs
for configuration, cap enforcement, frozen budget extensions, service admission,
Restore preview/confirmation, budget continuation and source fingerprinting.
Only that full historical fingerprint may add the new `cost_limits.py` module.
No current module is normalized and unknown historical identities cannot use
this addition. Tests compare actual immutable Git source and mutate every
accepted target module independently. The target is the immutable funding release
`14a32d0f95c62950203da4bda2d55be0318fb821`, not arbitrary later harness code.

The `tools_v8` compact-integer model format is deliberately outside that migration.
New protocols use co-located offer quotes and a same-request reference for an exact
duplicate latest receipt. V7 is retired for new execution: its frozen interface
fails with `AGENT_PROTOCOL_INTERFACE_RETIRED` under the v8 executor. Pre-compaction
and v7 runs retain their original prompts, schemas and
IDs and require their retained executor. Identical native bytes permit native
evidence reuse, not silent adoption of a different agent request format.

This is an explicit funding/admission-policy upgrade, not a claim that unlimited
spending existed in the old release. Ordinary Restore preserves the original
caps. A separately confirmed budget continuation changes only the dollar
allowance in its child; the historical source hash and original protocol bytes
remain intact. Private child receipts bind the exact current implementation.
Budget receipts bind both the original protocol used for the initial replay and
the exact funded protocol saved in later child checkpoints. A different cap,
model or prompt cannot reuse that receipt, even with a rehashed checkpoint.
The older 1× catalogue stays pinned to its original target; the native runtime
comparison still prevents migrating those runs into the current 16× release.

### Frozen run-configuration upgrade

The full implementation fingerprint of the deployed #69 source
`9dcd8bf5878338e090dc3363f5dd9d8d7bbd3893` has a separate one-way migration.
It pins the exact old and accepted whole-module ASTs for
`harness/context/freeze.py` and `harness/runtime.py`: new roots freeze their public
deck/stake in the stable rules prefix, and continuation admission checks the pair
**only when that original snapshot contains it**. No comparison file or method
is omitted, no current-side normalization is added, and the native manifest is
byte-identical. Unlisted historical fingerprints and changed/reverted target
modules cannot inherit this approval.

This preserves legacy execution inputs rather than adding missing information to
them. A legacy snapshot's original rules prefix, absent `run_configuration` field,
protocol hash and source hash survive Restore and subsequent branches unchanged.
New snapshots retain their pair. A separately confirmed budget extension still
changes only the dollar allowance, not either snapshot's prefix or pair. Saved
defaults and dashboard drafts do not supply continuation context.

The existing `restore-source-v1` receipt, operator acceptance, release/environment
admission and single checked replay are still required. Direct branching from an
old-source root still refuses; an admitted Restore child can carry its proof into
a branch. Tests compare immutable Git source, reject mutations on both sides and
exercise legacy/new Restore, branch and budget children with mock providers.
These are offline compatibility checks, not new native certification or evidence
of model quality. `tools_v8` remains the only active interface; v7 and previously
incompatible sources are not admitted by this migration.

### Claude provider addition

The Claude addition pins five exact module changes from deployed `d30b772`:
configuration/cache-rate validation and public presets, Anthropic's count fields,
provider-specific settings dispatch, the Anthropic adapter, and the new Claude
catalogue. It also composes with the exact `9dcd8bf` deck/stake migration above.
The native manifest, OpenAI adapter, shared transport, spending ledger, frozen
protocol and decision/runtime paths remain unchanged.

This mapping applies **only** when the frozen protocol uses OpenAI or a baseline
without a provider model. The receipt records `preserved_provider`, rechecks the
exact historical source and current target ASTs, and binds that scope to the
original protocol at execution. It does not infer the provider from current
dashboard settings, omit provider files, normalize current code, or admit
unlisted historical sources. Mutation tests cover every changed target and the
new module. Original prompts, models, efforts, prices, caps and source hashes stay
frozen. Operator acceptance and one checked native replay remain separate gates.

Historical Anthropic runs refuse this migration: adaptive thinking and caching
can change provider behavior, even with the same V8 game tools. They need their
retained executor or a separately reviewed compatibility proof. New Claude runs
can Restore under their matching source and frozen configuration as usual.

### Anthropic workspace routing

The exact deployed Claude source `3f953d9` may adopt the two authentication-only
changes in `harness/input_limits.py` and `harness/transport/anthropic.py`: reuse
the provider's header builder for counting, and optionally send the configured
workspace ID on both count and generation requests. Request bodies, model
settings, prompts, tools, signed thinking, budgets and native execution are
unchanged. This mapping applies to all providers from that exact source and
composes with the previously allowed OpenAI/baseline migrations above; it does
not newly admit pre-Claude Anthropic runs.

Both old and accepted whole-module AST hashes are pinned. Unknown historical
fingerprints, missing modules, reverts and further target edits refuse admission.
The original protocol and journals remain immutable, and the existing receipt,
operator consent, release/environment and single-replay gates still apply.
Workspace routing is backend authentication like the API key, not frozen
model-facing configuration; the value is never written into the protocol.

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
prompt, knowledge or original paid cap is rewritten. Only an explicit budget
intervention may set a different dollar limit for its child.
