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
registered separately, including budget-continuation and restore admission. There are 24
mounted routes flag-off, 43 flag-on, and 19 workbench-only routes.

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
numbers start at 1; journal IDs and workbench trajectory numbers are zero-based.

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

`bh summarize --episode-id EPISODE_ID --output summary.json
--markdown-output summary.md` writes a new public JSON decision ledger and an
optional ante-grouped Markdown recap. Reading the whole run records review
exposure; the command contacts no game or provider and refuses existing output
files. Recorded model notes remain claims, separate from observed transitions.

### Restore unfinished runs

Open an unfinished run in **Decision Explorer**, then **Restore run**. The
workbench-only control first fetches a read-only plan: latest saved pre-decision
boundary, spend across all attempts, original episode/batch caps, remaining
allowance, and whether an explicit compatible-code update is required. It does
not launch a game or contact a provider. Confirm paid execution and, when
shown, the compatible-code update before **Restore and continue**. A stale
plan or changed funding requires refreshing and confirming again.

The worker creates a separate `assistance: restoration` child, never scored as
an autonomous evaluation. The parent journal, annotations, checkpoint and frozen
protocol remain unchanged. One checked seed-prefix replay reaches the saved
boundary in the same process that continues playing; there are zero preliminary
verification launches. Public/private divergence or unknown action status stops
before a provider call. New decisions keep the original model, prompt, knowledge,
memory policy and limits. Later helper work beyond the saved boundary is not
reconstructed, but every attempted call remains charged, including unknown-usage
reservations. Restore children share the root's spending ledger and provider-call
allowance; neither retries nor code updates reset a cap.

GET `/api/operator/episodes/{eid}/restore` returns the plan or a named refusal.
POST requires its `parent_head` and `plan_hash`, plus strict booleans
`authorize_paid` and `accept_compatible_update` when applicable. Both routes require
the operator token and workbench flag. Confirmation switches to live status.

Supported parents are standalone failed, interrupted or unterminated runs and
their restoration children with a safe latest checkpoint. Completed games, an
already-completed restoration, campaign members, evaluator fixtures, budget
extensions/other intervention lineages, unsettled actions, missing evidence,
incomplete terminals, incompatible source and exhausted original caps return
explicit refusals. A sibling restoration still in progress also blocks admission.
There is no silent rewind or reuse of a discarded future. Dollar-cap increases
remain the distinct budget-continuation workflow below.

Older runs require the [source-compatibility proof](evidence.md#older-run-source-compatibility),
not rewritten source hashes. A passing plan is admission evidence, not a claim
that the upcoming replay has already succeeded.

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
# Optional diagnostic, not a start prerequisite: three authorized unpaid launches.
bh continue-budget EPISODE_ID --verify
# Explicit provider funding: replay once, then continue in the same game process.
bh continue-budget EPISODE_ID --start --combined-cap-usd 10
```

The default plan contacts no game/provider and distinguishes a saved checkpoint
from readiness for a single checked restore. Reading status records review exposure.
Verification and starts use the operator-protected worker so the dashboard can
stop the child. Verification does not grant spending permission; each start
requires an explicit cap higher than the root's original cap, not necessarily
higher than a prior child's. Action/call limits and batch slots are not extended.
Diagnose a timed-out request before retrying; timeout does not prove that the
worker stopped or that the action was not committed.
The child's exported metadata includes its recovery policy, cap, source identities,
admission ledger hash, and `root_batch_cap_usd` (the original campaign ceiling,
distinct from `previous_cap_usd`, the original episode cap). Provider-call totals
include inherited calls; the child's terminal cost records its own spend only.
The passing initial-blind continuation fixture does not override any historical
direct-save replay failure or certify later checkpoints.
