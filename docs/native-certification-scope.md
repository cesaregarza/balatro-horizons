# Match verification to the change

Native certification demonstrates game isolation, action execution, public
visibility, replay, and restoration for a pinned runtime. It does not validate
prompt quality, notebook use, or provider behavior. Repeating the native suite
after a change confined to those harness features provides no new evidence
about the changed behavior.

| Change | Required evidence |
| --- | --- |
| Prompts, context assembly, notebooks, provider handling | Relevant offline tests; explicit evidence reuse when the full source identity changes |
| Dashboard presentation | Frontend checks and a browser check; no game relaunch |
| Game/mod/bridge files, public projection, action validation, native environment defaults, persistence | Relevant native checks; full certification when changing the certified runtime or broad native contracts |
| Execution sequencing, settling, replay or recovery behavior, including changes in runner/service code | Relevant native checks even when the modified file is outside the native manifest |

Ordinary native gameplay checks share a process and use menu/start for a new
game. Relaunches are reserved for startup, restoration and explicit crash
recovery. Always describe a suite's launch plan before running it.

## Explicit reuse for harness updates

`scripts/reuse_native_evidence.py` checks a previously accepted Git revision
against the candidate without executing old code or accessing Windows. It
requires the original immutable certificate, matching saved environment lock,
existing native artifacts, identical native source components, and a fresh
successful `scripts/check_offline.py --report PATH` report bound to the exact
candidate source. The operator must also review that excluded harness changes
do not alter native execution sequencing. A filename classification alone
cannot establish that.

The native manifest covers engine transport/normalization/replay, observations,
action validation, storage, contracts, and native configuration with its local
dependencies. Gate/provenance code and the fixture engine are checked offline.
The unchanged saved environment lock identifies the installed game, mods and
bridge. Reuse does not claim a fresh inspection of those Windows files: normal
startup still verifies their actual hashes and isolation before an episode.

Example from an isolated Linux candidate checkout:

```sh
.venv/bin/python scripts/check_offline.py --report private/offline-candidate.json
.venv/bin/python scripts/reuse_native_evidence.py \
  --root /path/to/live --candidate /path/to/candidate \
  --baseline PREVIOUSLY_ACCEPTED_COMMIT \
  --offline-report private/offline-candidate.json
```

The second command is a read-only preview. After confirming the worker is idle,
backing up the live files, stopping the workbench and installing the exact
candidate, repeat it with `--apply` before restarting the workbench. A changed
runtime lock or native component fails closed. Never copy a candidate hash over
the source identity in old evidence.

The new acceptance record preserves the original native-tested
`implementation_hash`, records the new `accepted_implementation_hash`, native
manifest, parent certificate and offline report hash, and reports zero native
launches. Future harness edits require their own tests and explicit acceptance;
reuse is not blanket approval. Original immutable records remain intact.

Checkpoint certificates and agent protocol snapshots continue to require the
full implementation identity. Reusing environment evidence does not make old
checkpoints valid under a changed harness or establish new restoration coverage.
