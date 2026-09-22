# Dashboard

The dashboard is a typed browser client over a Python gateway. `web/src/api`
owns the HTTP boundary, screen components own views, and `App.tsx` only
coordinates navigation, tokens, polling, and screen state. The server owns
privacy projection and decision export; the client only triggers a download.

## Surface boundary

The factory defaults `Config.workbench_enabled` to false; `bh review --workbench`
explicitly enables it. The ordinary
dashboard always mounts the run library, live status, decision exploration,
cost/model-budget settings, batches, reports, and cheap append-only
retrospective annotations under the `/api/explore...` and core namespaces.
These surfaces remain usable when the flag is off: 23 mounted routes including
the root page, versus 39 with the 16 workbench routes enabled.

The workbench owns staged reveal/review, branching and comparison, human
takeover, and verification. Its `/api/review*`,
`/api/branches*`, `/api/operator/human`, and `/api/verify` routes are absent and
return 404 when the flag is off. Workbench controls and screens are hidden in
that mode; exploration and annotation are not. DevTrace is a development
surface under Decision Explorer, not a separate navigation root.

## Runtime and safety

The gateway is assembled from route modules, shared middleware, and a factory.
The CLI accepts only `--host 127.0.0.1` or `--host localhost`; the backend stays
on loopback behind Tailscale Serve. `--public-origin` names that exact trusted
Tailscale HTTPS origin. Credentials belong in the backend
environment, never browser settings. Private run directories, raw observations,
seeds, saves, provider requests, and reviewer identity are not browser data.
The trusted public origin must be HTTPS on a `.ts.net` hostname.

The server projects exact public schemas for JSON and JSONL exports and scans
them before writing. A browser cannot reconstruct an export from detail records.
Decision downloads use GET `/api/explore/export/{format}` or its workbench
`/api/review/export/{format}` counterpart; no client-supplied ledger is accepted.
A download reads the current server snapshot, which may be newer than the last
browser poll; its source journal head identifies the exported snapshot.
Action labels come from the single packaged `review/action_descriptors.json`,
also imported by TypeScript. Route names and accessible labels are versioned UI
contracts; changes require matching browser coverage.
Unknown edition text is escaped and never used as a CSS class. Explorer decision
numbers start at 1; journal IDs and workbench trajectory numbers are zero-based.

Status polling reads a compact per-episode cache, not the full journal on every
request. Device, inode, size, modification time, or change time invalidates the
entry; concurrent readers share verification and changing journals are read
under the writer lock. The cache retains no observations or provider bodies.
Terminal journal facts override a lagging SQLite index, pending reservations
remain in costs, and unchanged polls append no duplicate exposure. Browser polls
never overlap, stop when hidden or disabled, and event-stream work stays off the
server event loop. Use `uv run bh review status --timing` for a bounded check.

After WSL or service recreation, preview and then apply only the required Windows
session variables from a connected WSL shell:

```bash
uv run bh review session
uv run bh review session --apply
```

The apply step writes a service-only drop-in and restarts the idle service; it
rejects a busy native worker and never imports shell credentials, proxies, or the
general environment. Reapply after the service is recreated. Loopback plus an
operator-configured Tailscale Serve endpoint remain the supported remote path.

## Review and continuation

The workbench records exposure before a reveal, keeps cursor movement explicit,
and appends annotation revisions. Prospective annotations are filtered to the
revealed decision boundary. Budget continuation is deferred to PR #25; there is
no `continue-budget` route in this cutover. Loopback and route isolation apply
equally to operator controls.
Explore and staged-review tokens use separate stores: an explore token cannot
advance a workbench cursor. Retrospective annotation lists use the explicitly
selected decision boundary without mutating the read-only explore cursor.

`bh summarize --episode-id EPISODE_ID --output summary.json
--markdown-output summary.md` writes a new public JSON decision ledger and an
optional ante-grouped Markdown recap. Reading the whole run records review
exposure; the command contacts no game or provider and refuses existing output
files. Recorded model notes remain claims, separate from observed transitions.
