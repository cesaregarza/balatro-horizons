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
These surfaces remain usable when the flag is off; workbench-only routes are
registered separately, including budget-continuation admission. There are 24
mounted routes flag-off, 41 flag-on, and 17 workbench-only routes.

The workbench owns staged reveal/review, branching and comparison, human
takeover, budget continuation, and verification. Its `/api/review*`,
`/api/branches*`, `/api/operator/human`, `/api/operator/episodes/{eid}/continue-budget`,
and `/api/verify` routes are absent and
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
Decision ledgers and `bh summarize` contain only the selected episode's actions,
with `action_accounting` separating own, verified inherited, and total commits.
Runner terminal counts include ancestry; crash recovery and pre-start failures
count only their own journal, identified by `terminal_count_scope`. Decision IDs
and stored summaries remain unchanged; parent rows are never silently duplicated.
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

The apply step writes an owner-only registration without restarting the service;
it rejects a busy native worker and never imports shell credentials, proxies, or
the general environment. Reapply when the registered terminal/socket expires.
The operator-only `/api/operator/runtime` endpoint and `bh doctor` report safe
registration status, not game readiness or certification. The run form checks
this status and disables native launch until it is ready; synthetic runs remain
available. Use **Refresh connection** after registering. Loopback plus an
operator-configured Tailscale Serve endpoint remain the supported remote path.

## Review and continuation

The workbench records exposure before a reveal, keeps cursor movement explicit,
and appends annotation revisions. Prospective annotations are filtered to the
revealed decision boundary. Loopback and route isolation apply equally to
operator controls, including explicit budget continuation.
Explore and staged-review tokens use separate stores: an explore token cannot
advance a workbench cursor. Retrospective annotation lists use the explicitly
selected decision boundary without mutating the read-only explore cursor.

`bh summarize --episode-id EPISODE_ID --output summary.json
--markdown-output summary.md` writes a new public JSON decision ledger and an
optional ante-grouped Markdown recap. Reading the whole run records review
exposure; the command contacts no game or provider and refuses existing output
files. Recorded model notes remain claims, separate from observed transitions.

### Explicit budget continuation

A standalone root stopped by a dollar cap may create a child marked
`assistance: budget_extension`, never evaluation-eligible. The parent stays
budget-exhausted; a later win does not become an autonomous win at the old cap.
Model settings, prompts, tools, non-money limits, source identity, and the
pre-decision memory boundary remain frozen. A changed implementation is refused.
Helpers called after that checkpoint are charged, but their later note changes
and tool results are not inherited by the child.

The combined cap includes the root and every continuation attempt. Unknown-usage
reservations remain spent when their owning terminal accounts for them. Admission
reconciles one locked ledger snapshot against the root and child terminals,
using admission hashes rather than SQLite index order. Zero-spend children do
not change the ledger hash. Lost ledger rows are refused; this is not a tamper-
proof store if both a child's ledger rows and its index entry are deleted.
Recover the index with `bh recover` before admission after index loss.
A crash between `spending.settle` and the `provider_response` journal write can
leave a permanent `BUDGET_EXTENSION_CHILD_SPEND_MISMATCH` after `bh recover`;
recovery cannot reconstruct the missing response's settled cost attribution.

Use the owning checkout and an idle backend started with `bh review --workbench`:

```bash
bh continue-budget EPISODE_ID --plan
# Separately authorized native access: three unpaid restoration/probe launches.
bh continue-budget EPISODE_ID --verify
# Separately authorized provider spending: one model-run launch.
bh continue-budget EPISODE_ID --start --combined-cap-usd 10
```

The default plan contacts no game/provider and distinguishes a saved checkpoint
from the required probe certificate. Reading status records review exposure.
Verification and starts use the operator-protected worker so the dashboard can
stop the child. Verification does not grant spending permission; each start
requires an explicit cap higher than the root's original cap, not necessarily
higher than a prior child's. Action/call limits and batch slots are not extended.
Diagnose a timed-out request before retrying; timeout does not prove that the
worker stopped or that the action was not committed.
The child's exported metadata includes its certificate, cap, source identities,
admission ledger hash, and `root_batch_cap_usd` (the original campaign ceiling,
distinct from `previous_cap_usd`, the original episode cap). Provider-call totals
include inherited calls; the child's terminal cost records its own spend only.
The passing initial-blind continuation fixture does not override any historical
direct-save replay failure or certify later checkpoints.
