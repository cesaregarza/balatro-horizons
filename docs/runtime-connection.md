# Windows runtime connection lifecycle

The Linux backend outlives individual WSL/Windows sessions. It must not treat
the environment inherited when systemd started it as a permanent connection.

From a Windows-connected WSL terminal in the project:

```bash
.venv/bin/python scripts/configure_workbench_session.py --apply
.venv/bin/python scripts/configure_workbench_session.py --check
```

Registration copies only the Windows launch-variable allowlist into an atomic,
owner-only Linux file at `private/windows-session.json`. It does not copy API
credentials or arbitrary `WSLENV` entries. It requires an owned interop socket
under `/run/WSL` and refuses to replace the registration during native execution.
It starts no processes and does not restart the backend. Older systemd session
drop-ins can remain during migration; bridge subprocesses now use the registered
context explicitly instead of inheriting those values.

Every newly opened bridge process reloads the registration. Expiration rejects
new native admissions before episode creation, so a failed connection is not
recorded as a model attempt. Browser, CLI, branches and batch dispatch use the
same preflight. Expiry after admission still terminates and records the attempt;
unresolved actions remain infrastructure failures and are never replayed.

The operator-only `/api/operator/runtime` endpoint and `bh doctor` expose a safe
connection status. The native run form polls that small status and disables
launch while disconnected. This verifies registration/socket availability, not
game readiness. Each actual launch must still verify runtime files, process
identity, instrumentation, save isolation and the frozen profile.

The connection can expire again when its owning Windows/WSL session ends. Run
the registration command from a current terminal to reconnect. There is no
automatic search of other users' sessions and no hidden game restart.

The launcher is tracked until readiness. A nonzero launcher exit fails promptly
as `NATIVE_LAUNCH_PROCESS_FAILED`; wrong process, save identity or instrumentation
errors keep their specific codes. Only temporary RPC unavailability is polled
during startup. Private `startup-failures/*.json` records preserve exit status
and the last safe error code without engine state, seeds or raw stderr.

## Verification scope

Offline checks cover absent, expired, malformed and re-registered connections;
secret filtering; admission without episode creation; launcher failure;
identity mismatch; and cleanup preserving the primary failure. Browser tests
cover disconnected admission and reconnection.

`verify_startup_lifecycle.py --plan` declares one process for unpaid Red/White
and Red/Gold terminal runs, followed by one deployed web startup. Gameplay cases
share the first process using menu/start. No restoration launches are required.

The optional `reuse_native_evidence.py --startup-report` path requires a passing
source-bound native report and a passing full offline report. Its AST check
allows only startup, explicit subprocess environment selection and startup
deadline/cleanup handling in the bridge; action encoding, response validation,
gameplay execution, masking, restoration and runtime files must remain unchanged.
Prior evidence and checkpoint certificates retain their original identity.
This acceptance records which layer was retested; it does not claim new
restoration or headless evidence.
