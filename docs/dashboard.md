# Dashboard

The dashboard is a typed browser client over a Python gateway. `web/src/api`
owns the HTTP boundary, screen components own views, and `App.tsx` only
coordinates navigation, tokens, polling, and screen state. The server owns
privacy projection and decision export; the client only triggers a download.

## Surface boundary

The factory defaults `Config.workbench_enabled` to false. The ordinary
dashboard always mounts the run library, live status, decision exploration,
cost/model-budget settings, batches, reports, and cheap append-only
retrospective annotations under the `/api/explore...` and core namespaces.
These surfaces remain usable when the flag is off.

The workbench owns staged reveal/review, branching and comparison, human
takeover, verification, and budget continuation. Its `/api/review*`,
`/api/branches*`, `/api/operator/human`, and `/api/verify` routes are absent and
return 404 when the flag is off. Workbench controls and screens are hidden in
that mode; exploration and annotation are not. DevTrace is a development
surface under Decision Explorer, not a separate navigation root.

## Runtime and safety

The gateway is assembled from route modules, shared middleware, and a factory.
The default bind is loopback. Remote binding requires an explicit override,
HTTPS, and a trusted `.ts.net` origin. Credentials belong in the backend
environment, never browser settings. Private run directories, raw observations,
seeds, saves, provider requests, and reviewer identity are not browser data.

The server projects exact public schemas for JSON and JSONL exports and scans
them before writing. A browser cannot reconstruct an export from detail records.
Action labels come from the shared descriptor table used by API summaries and
TypeScript presentation. Route names and accessible labels are versioned UI
contracts; changes require matching browser coverage.

## Review and continuation

The workbench records exposure before a reveal, keeps cursor movement explicit,
and appends annotation revisions. A budget continuation is admitted only for a
terminal parent at cap exhaustion after checkpoint, certificate, protocol,
knowledge, implementation, and spending-ledger checks. Its combined cap must
increase; the parent remains immutable and the assisted child is not autonomous
evidence. Loopback and route isolation apply equally to operator controls.
