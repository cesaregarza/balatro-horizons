# Cloud runner

The cloud runner runs each game on a throwaway DigitalOcean Droplet, one per run,
and guarantees the Droplet is deleted and verified gone afterwards, including
after an error, a crash or a lost worker. This first slice is the controller's run
lifecycle and its teardown guarantees. It has no worker payload, no
controller-to-worker channel and no deployment, so a real launch would boot an
empty Ubuntu Droplet and tear it down when the heartbeat grace expires.

The laptop and desktop native path is unchanged. The package lives in
`src/balatro_horizons/cloud/`, outside the execution manifest, so restore
compatibility is untouched.

## Commands

| Command | Provider calls | Purpose |
| --- | --- | --- |
| `bh cloud status` | none | Ledger, launch gate, budget, the last inventory read and active alerts. Exits 1 while an alert is active. |
| `bh cloud launch RUN_ID --allow-provider-calls` | yes | Start the controller, re-evaluate existing runs and the owned inventory, launch one run and supervise until every run is torn down. Exits nonzero if any alert is active at the end. |
| `bh cloud cancel RUN_ID [--allow-provider-calls]` | only with the flag | Without the flag, record the request for the controller. With it, also tear down now. |
| `bh cloud reconcile --allow-provider-calls` | yes | Tear down expired runs and orphans, resolve unresolved creates, and verify each. Safe from a timer while the controller is down. |

Every command that can reach DigitalOcean refuses with
`PROVIDER_CALLS_NOT_ALLOWED` unless `--allow-provider-calls` is given, as
`--allow-paid` does for model calls. The token is read from `DIGITALOCEAN_TOKEN`
when the client is built and is held only in the HTTP client's headers. It is
never logged, written to the ledger, sent as user data or printed.

Settings live in `configs/cloud.yaml`, not `config.py`; pass another file with
`--cloud-config`. The ledger lives under `--data-dir`, in `cloud/`.

## Run lifecycle

1. **Persist first.** The run and its absolute UTC deadline
   (`now + max_run_hours`) are written before any provider call.
2. **Create.** One Droplet is created with two tags: the owner tag
   `bh-owner:<controller_id>` and the run tag `bh-run:<run_id>`.
3. **Boot.** The run becomes `running` when the Droplet is `active`.
4. **Supervise.** Each tick re-reads the ledger and tears the run down on the
   first reason that applies.
5. **Tear down and settle.** Verified teardown settles the elapsed time against
   the infrastructure budget.

| Trigger | Teardown reason |
| --- | --- |
| The worker reports it finished | `succeeded` |
| Not `active` within `boot_timeout_seconds`, or gone during boot | `boot_failed` |
| The create call is rejected with a 4xx, or an ambiguous create resolves | `create_failed` |
| The absolute deadline passes | `deadline` |
| A cancel request | `cancelled` |
| No heartbeat within `heartbeat_grace_seconds` | `heartbeat_lost` |
| Any error or interrupt in the supervising loop | `controller_error` |
| A run still provisioning when a controller starts | `restart_while_provisioning` |
| An owner-tagged Droplet with no live ledger run | `orphan` |

On a controller restart, every live run is re-evaluated. A provisioning run is
torn down, never adopted; one with no recorded Droplet ID becomes an unresolved
create, because the crash may have followed an accepted create. A running run is
torn down if it is past its original deadline; otherwise it gets a fresh
heartbeat grace window and is torn down if no heartbeat arrives. An interrupted
teardown resumes. Only one controller can run at a time: it holds
`controller.lock` for its lifetime, and a second one exits with
`CONTROLLER_BUSY`.

At startup, and again just before admitting each launch, the controller reads the
whole owner-tag inventory, tears down orphans and records unmatched Droplets. If
that read fails or is malformed, the launch is refused with
`INVENTORY_UNVERIFIED`, and `status` shows the failed read.

## Ambiguous creates

A lost response, timeout, dropped connection, malformed reply or 5xx on create
may follow an accepted create. A 4xx is the only definite rejection. An ambiguous
create moves the run to `create_unresolved`: it keeps its reservation and its
concurrency slot, records a `CREATE_UNRESOLVED` alert, and refuses launches.

It resolves only on positive evidence:

1. Its Droplet is found, by run tag or by its exact name through the name filter,
   carrying both tags, and its deletion is verified.
2. Or, once `create_settle_seconds` (default 10 minutes) have passed since the
   attempt, the account action log has been read completely, back to five
   minutes before the attempt to allow for clock skew. Every Droplet create in
   that window is then resolved by ID. A 404 means gone. A Droplet carrying this
   run's tags is deleted and verified. A Droplet with other tags is left alone.
   The run-tag and name listings must also be empty. A Droplet with this run's
   name but not its tags keeps the run unresolved.

If the action log or any listing cannot be read completely, the run stays
unresolved. The basis is that listings can lag a fresh create, while the action
log records the create itself, and the API keeps completed actions for querying.
The API documents no order for the action log, so every page is read. A read is
complete only when the page count stays within bounds and the items collected
match `meta.total`.

Listings are strict: a missing or wrongly typed `id`, `name`, `status`, `tags`,
`created_at`, pagination link or `meta.total` makes the listing fail. It is never
read as empty.

## Verified teardown

Teardown deletes every recorded Droplet and every Droplet listed under the run tag
that also carries the owner tag. Deletion is by ID only; the bulk delete-by-tag
endpoint is never called. Before each delete, the Droplet is read by ID. A 404
means gone. A Droplet that no longer carries both tags is not deleted: it is
handed to the unmatched watch described below, with its alert and the closed
launch gate. Each pass is then verified: every ID must answer 404,
and the run-tag listing must be empty. Powering a Droplet off never counts,
because it still bills. Failed passes retry with exponential backoff
(`retry_base_seconds`, capped at `retry_max_seconds`).

If a run is not verified within `cleanup_verify_seconds`, it is marked
`cleanup_unverified`, an alert is recorded under `cloud/alerts/`, and every new
launch is refused. There is no override. Only a later verified absence, from a
controller tick or `bh cloud reconcile`, clears it.

Resources without the owner tag, or with another controller's owner tag, are
never deleted.

An owner-tagged Droplet without exactly one usable run tag is never deleted
either, because the deletion filter needs both tags. It may still be billing, so
the reconciler records an `UNMATCHED_DROPLET` alert, `status` shows it, and new
launches are refused. Only a reconcile that sees it gone clears this: a 404 by
ID and its absence from the owner-tag listing. Delete it by hand after checking
what it is.

An orphan with no ledger run settles from the provider's `created_at`, or from
when it was found if that time is missing or unparseable.

## Budget

Infrastructure spend is bounded separately from model spend. A launch is refused
when its worst case, `max_run_hours × hourly_price_usd`, exceeds the remaining
`infrastructure_budget_usd`. Live runs count at the larger of their reserved
worst case and their elapsed cost; torn-down runs count at their settled cost.
`max_concurrent_workers` is 1 until parallel runs arrive.

## Ledger

The ledger holds one JSON file per run in `cloud/runs/`. Each write survives power
loss, not just a process crash: a temporary file in the same directory is written,
flushed and synced, renamed into place, and then the directory is synced. A new
directory is synced into its parent. A crash at any point leaves the previous
version readable. The intent record written before a create holds the run ID,
the original absolute deadline and the spend reservation. Alerts are separate, append-only files. The controller
and the reconciler serialize on `cloud/ledger.lock`. The controller holds it for
one operation at a time and waits up to 30 minutes for it. The reconciler waits
30 seconds and then exits with `LEDGER_BUSY`.

## Limits

- No worker payload, channel or heartbeat transport exists yet. The worker
  channel is an interface, and real launches use a silent channel.
- Only Droplets are created. The ledger records each resource's kind so firewalls
  and SSH keys can join later.
- Unverified: the default size `s-2vcpu-8gb-amd` (2 vCPU, 8 GiB), its hourly
  price of 0.063 and the region `nyc3` have not been checked against the live
  API. A later real-provider smoke test, which needs the owner's approval,
  confirms them.
- All tests use a fake provider or a fake HTTP transport. No real provider call
  has been made.
