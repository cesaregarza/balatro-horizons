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
registered separately, including budget-continuation and restore admission. There are 25
mounted routes flag-off, 46 flag-on, and 21 workbench-only routes.

The workbench owns staged reveal/review, branching and comparison, human
takeover, budget continuation, and verification. Its `/api/review*`,
`/api/branches*`, `/api/operator/human`, `/api/operator/episodes/{eid}/continue-budget`,
`/api/operator/episodes/{eid}/restore`,
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
If execution has started but the runner cannot write its terminal, the service
records `EXECUTION_TERMINAL_INCOMPLETE`. Decision exports refuse that same code
before review exposure: fallback zeroes are not reconstructed accounting totals.
A download reads the current server snapshot, which may be newer than the last
browser poll; its source journal head identifies the exported snapshot.
Action labels come from the single packaged `review/action_descriptors.json`,
also imported by TypeScript. Route names and accessible labels are versioned UI
contracts; changes require matching browser coverage.
Unknown edition text is escaped and never used as a CSS class. Explorer decision
numbers, annotation controls, branch provenance and workbench trajectories start
at 1. API and journal IDs remain zero-based; no historical records are renumbered.

## Finding and navigating runs

The library starts in **Blinded review**. **Operator details** explicitly reveals
model identity and outcomes and records exposure before returning those fields.
Search/filter by model, effort text, harness text, deck/stake, status and evidence;
hide test/fixture records, sort, and page through continuation families. Each
continuation keeps its own Explore, Review and comparison actions. Select two
records for a descriptive comparison; missing metadata remains unknown, and
independent outcomes do not establish causal effects.

Run/decision navigation has meaningful browser Back/Forward entries. Tabs retain
the selected decision, filters, scroll and in-memory drafts. Empty inspector tabs
link back to the library. **Go to decision** is an exact one-based jump, separate
from text search. Phone deep links open the requested detail. Unsaved assessments
are guarded before replacing their editor/run and are not silently retargeted by
history navigation. Seed drafts remain only in memory, never local storage.

**Start a run** jumps to a preserved launch draft. The confirmation summary shows
model/effort, real versus synthetic mode, deck/stake and both effective dollar
ceilings. Per-run model settings are validated against the configured model and
applied to a copy; only **Save model defaults** writes persistent defaults. A new
run or continuation opens its explorer. Worker status is explicitly revealed;
active/recent cards open runs, and idle Stop controls are disabled.

The ordinary explorer includes unfinished requests and links to a failed final
attempt. Spend remains first; exports, legends and debugging are secondary.
Ordinary detail requests use `technical=false`, retaining boards and returned
reasoning summaries without heavy request/helper bodies. **Exact public decision
records** fetches those records only when opened. The legacy detail response and
download schemas remain unchanged.

Explorer reads reuse hash-verified journal snapshots while device, inode, size,
mtime and ctime are unchanged. This separate cache admits journals up to 96 MiB,
retains at most four snapshots and 32 MiB of compressed data total, and falls back to normal
verified reads above those bounds. Each snapshot includes an ordinary display
projection so board navigation does not decode heavy technical bodies. Accounting,
exports and exact records still use the full verified data. Cached results are decoded afresh so a caller
cannot mutate later reads. Library harness labels use a bounded, hash-checked
genesis reference and frozen protocol bundle; this is display provenance, not
certification of the rest of the journal. Missing historical metadata stays unknown.

For a repeatable read-only browser check against an isolated server/store, run
`BH_WORKBENCH_URL=http://127.0.0.1:8768 node web/scripts/verify_browser_native.mjs --explore EPISODE_ID 20`.
It reports first/next board timings and detail bytes, checks desktop and phone
deep links, and saves screenshots under `reports/verification/`. It records normal
inspection exposure in that store but refuses non-inspection mutation requests.

Status polling reads a compact per-episode cache, not the full journal on every
request. Device, inode, size, modification time, or change time invalidates the
entry; concurrent readers share verification and changing journals are read
under the writer lock. The cache retains no observations or provider bodies.
Terminal journal facts override a lagging SQLite index, pending reservations
remain in costs, and unchanged polls append no duplicate exposure. Browser polls
never overlap, stop when hidden or disabled, and event-stream work stays off the
server event loop. Use `uv run bh review status --timing` for a bounded check.

Decision Explorer leads with **API spend (USD)**, refreshed by its existing live
poll, and live-status cards show the same accounting. Ordinary totals are episode-only;
restoration children lead with prior-attempt spending plus the current attempt,
with both amounts labeled separately. The current-attempt total
includes recorded response costs plus pending/unknown-usage reservations;
the breakdown labels both. Response costs are harness estimates and may retain
the reservation when usage is unavailable: this is not a provider invoice or
in-game cash. Terminal totals remain authoritative; missing historical detail
shows an unavailable breakdown rather than inventing zero spend. Explorer
marks paused/interrupted updates beside the last recorded total. No costs are
added to the blinded library or staged prospective review, and journals,
budget enforcement, and decision-download schemas are unchanged.

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
Staged review retains a separate high-water cursor. **Previous revealed decision**
and **Return to latest revealed** revisit only already-revealed information;
future decision IDs and unrevealed actions/consequences remain unavailable.
`POST /api/review/revisit` does not relax retrospective-only seek/detail access.

`bh summarize --episode-id EPISODE_ID --output summary.json
--markdown-output summary.md` writes a new public JSON decision ledger and an
optional ante-grouped Markdown recap. Reading the whole run records review
exposure; the command contacts no game or provider and refuses existing output
files. Recorded model notes remain claims, separate from observed transitions.

### Restore unfinished runs

Opening a stopped run checks recovery eligibility read-only. Completed games do
not advertise Restore; unavailable plans explain the next step with the technical
code in a disclosure. Use **Decision Explorer → Restore run**, then explicitly
authorize the continuation and any compatible-code update. Restore creates an
unscored child through one checked replay; it never rewrites the parent or resets
its funding. See [run recovery](run-recovery.md#restore-unfinished-runs) for
eligibility, API fields, immutable provenance and named refusals.

### Explicit budget continuation

New model runs offer **Current limits**, **$10 total** and red, confirmed
**Uncapped**. For a cost-stopped standalone root, **Review cost override** offers
**$10 more** (all-attempt spend plus $10) or confirmed Uncapped. These are per-run
choices, never saved defaults or batch settings. Uncapped still has a finite
reservation envelope (remaining provider calls × maximum per-call reservation),
and must be confirmed again for Restore or every branch mode. See [funding controls](run-recovery.md#funding-controls)
for exact accounting, effective bounds, preview eligibility, API and CLI consent.
