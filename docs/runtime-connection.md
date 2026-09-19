# Windows runtime connection

## Persistent backend credentials

The user service must not depend on a temporary key file that can disappear when
WSL restarts. Prepare a persistent, owner-only environment file from an existing
private file on the native Linux filesystem:

```bash
.venv/bin/python scripts/configure_backend_credentials.py --source /path/to/private/provider.env
.venv/bin/python scripts/configure_backend_credentials.py --source /path/to/private/provider.env --apply
systemctl --user daemon-reload
# Restart only while the worker and native verifier are idle.
systemctl --user restart balatro-horizons.service
```

The first command previews names and paths without writing or printing values.
The helper accepts only provider key names, stores them in
`private/providers.env` with mode 0600, and writes a user-service drop-in that
references the persistent file. With no `--source`, it preserves existing
credentials or creates an empty placeholder so the unpaid dashboard can start.
No mode calls a provider. Paid admission still requires a key and explicit
spending limits. Do not place credential values, source files, or key paths in
commits or support logs.

## Session registration

The Linux backend can outlive a Windows-connected WSL terminal. Register the
current connection from such a terminal without restarting the backend:

```bash
.venv/bin/python scripts/configure_workbench_session.py --apply
.venv/bin/python scripts/configure_workbench_session.py --check
```

Registration stores only an allowlist of Windows launch variables in an atomic,
owner-only `private/windows-session.json`. It requires an owned interop socket
under `/run/WSL`, refuses replacement while the native worker is busy, and
starts no game or other process. It copies neither provider keys nor arbitrary
`WSLENV` entries. The bridge passes only this registered context and a small
Linux allowlist (`PATH`, `LANG`, `LC_ALL`) to Windows subprocesses; it does not
inherit the backend's provider-key environment.

The operator-only `/api/operator/runtime` endpoint and `bh doctor` report a
safe status. The run form polls that status and leaves native start disabled
until the registration is ready; synthetic tests remain available. Native run,
branch, and batch admission checks the registration before creating an episode.
An expired session must be registered again from a current terminal. Status
proves only registration and socket availability, not game readiness or native
certification. A connection can expire after admission; any resulting failure
remains an infrastructure failure and must not be replayed as a game action.
