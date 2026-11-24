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
| `bh cloud reconcile --allow-provider-calls` | yes | Tear down expired runs and orphans, look for the Droplets of unresolved creates, and verify each. Safe from a timer while the controller is down. |

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
torn down, never adopted; one with no recorded Droplet ID is held as an unresolved
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

A create has a known outcome only when a Droplet ID is recorded for it, or when
the API answered it with a 4xx, the only definite rejection. Anything else, from
a lost response, timeout, dropped connection, malformed reply or 5xx to a
controller crash before the ID was written, may have followed an accepted
create. The run is persisted before the request is sent, so the ledger always
knows a create was attempted.

Such a run moves to `create_unresolved`. It keeps its reservation and its
concurrency slot, shows a `CREATE_UNRESOLVED` alert, and refuses launches.
Every teardown path holds it this way rather than tearing it down: the
controller's tick, boot check and restart recovery, the reconciler's expiry and
boot-timeout checks, `cancel` with or without `--allow-provider-calls`, the
deadline, and the error path of the supervising loop. None of them settles the
run on empty listings and no recorded IDs.

The evidence standard is a sighting. The run is released only when its Droplet
is found carrying both tags, and its deletion is verified as for any teardown.
It is looked for three ways on every controller tick and reconciler pass:

1. In the run-tag listing.
2. By its exact name, through the name filter.
3. By ID, for each Droplet create the account action log names since five
   minutes before the attempt, to allow for clock skew.

Nothing else clears the hold, however long it lasts. Empty listings prove
nothing, because a fresh Droplet can be missing from them for a while. An action
log create that is in progress, has no resource ID yet, or names an ID that
answers 404 proves nothing either, because the create may still be under way. A
read that fails or is malformed counts as nothing found. A Droplet that carries
the run's name but not both tags is never touched.

The backstop is that both tags go on in the create request itself, so the
sweeps delete such a Droplet whenever it appears. While its run is held, the
run-tag and name reads above find it. The owner-tag inventory sweeps, at
startup, before each launch and on each reconciler pass, adopt and delete any
owner-tagged Droplet whose run is no longer live.

Clearing a create that the provider never shows is one of the open cases listed
under [Cases that hold](#cases-that-hold). In this slice such a run holds launches
and its reservation indefinitely, which is the intended fail-closed result.

The API documents no order for the action log, so every page is read. A read is
complete only when the page count stays within bounds and the items collected
match `meta.total`. An action with a status other than `in-progress`,
`completed` or `errored` makes the read fail.

Listings are strict: a missing or wrongly typed `id`, `name`, `status`, `tags`,
`created_at`, pagination link or `meta.total` makes the listing fail. It is never
read as empty.

## Verified teardown

The controller believes a Droplet is gone only after the provider has
acknowledged our DELETE for it. DigitalOcean documents no read-after-write
guarantee, so the controller relies on none: a by-ID read and a listing can
each lag or disagree. An ID counts as gone only when all three of these hold,
in order:

1. A fresh by-ID read showed it with both tags.
2. The provider acknowledged our DELETE with a 2xx. A 404 on DELETE is not an
   acknowledgment. The acknowledgment is recorded durably on the run's resource
   before any later read is trusted.
3. Afterwards, a by-ID read returns 404 and the run-tag listing omits the ID.

Before the acknowledgment, a 404 or an empty listing proves nothing. The run
keeps its state, reservation and slot, and later passes read again; if the ID
reads with both tags again, it is deleted then. The same rule applies to every
run: a known ID, an adopted orphan, and a Droplet found for an unresolved
create.

Teardown deletes every recorded Droplet and every Droplet listed under the run tag
that also carries the owner tag. Deletion is by ID only; the bulk delete-by-tag
endpoint is never called. Powering a Droplet off never counts, because it still
bills. Failed passes retry with exponential backoff (`retry_base_seconds`, capped
at `retry_max_seconds`).

If a run is not verified within `cleanup_verify_seconds`, it is marked
`cleanup_unverified`, status shows a `CLEANUP_UNVERIFIED` alert, and every new
launch is refused. There is no override. Only a later verified absence, from a
controller tick or `bh cloud reconcile`, clears it.

Resources without the owner tag, or with another controller's owner tag, are
never deleted.

### IDs the controller may not delete

Two kinds of Droplet are never deleted, because the deletion filter needs both
tags:

- A recorded ID whose fresh read no longer shows both tags. The unmatched watch
  is written first, and only then is the ID taken out of the run's deletable
  set. A crash between the two writes leaves the ID in both places, and the
  next pass repeats the step.
- An owner-tagged Droplet without exactly one usable run tag, found by the
  reconciler.

Each is recorded in the unmatched watch, `status` shows an `UNMATCHED_DROPLET`
alert for it, and new launches are refused. With no DELETE of ours to acknowledge, no read clears them: a 404
does not, and neither does absence from a listing. A run that holds such an ID
never settles, so it keeps its reservation and its slot. Delete the Droplet by
hand after checking what it is.

### Cases that hold

These hold launches, and the run's reservation where there is one, until an
operator path is designed in a later slice:

- A create the provider never shows.
- A DELETE whose acknowledgment was lost, for example to a crash before it was
  recorded.
- A Droplet deleted outside the controller, which reads 404 with no
  acknowledgment.
- An unmatched Droplet.

A completed `destroy` action for the ID in the account action log is a candidate
for conclusive evidence in some of these cases. It is not implemented; the first
real-provider slice checks whether it is reliable.

Live runs need an evidence-backed recovery path for these cases first, with no
force-clear. Until then they are a documented limit of this offline slice: a lost
DELETE acknowledgment, for example, blocks launches indefinitely even when the
deletion really happened.

### Alerts

Status derives its alerts, and its exit code, from the durable records that block
launches, through the same function the launch gate uses: each run in
`create_unresolved` or `cleanup_unverified`, each unmatched Droplet, and an
inventory read that did not complete. The run's transition history and the
watch's `found_at` record when each began. No separate alert file is written, so a
crash right after a blocker is committed cannot hide it. Capacity refusals, from
the concurrency limit and the budget, are not alerts, and status exits 0 for them.

An orphan with no ledger run settles from the provider's `created_at`, or from
when it was found if that time is missing or unparseable.

### Status in the dashboard

The dashboard's Cloud runs tab shows the same report. `bh cloud status` and
`GET /api/cloud/status` call one function, so the alerts, refusals, budget and
per-run costs cannot disagree. Each run reports `cost_usd`, the launch gate's own
estimate (the settled amount once torn down, otherwise the larger of the
reservation and the elapsed hours at the hourly price), next to `reserved_usd` and
`settled_usd`. The report also carries `read_at`. The dashboard reads the ledger
and the default settings file on every request, takes no lock and creates nothing,
and answers `CLOUD_LEDGER_UNREADABLE` or `CLOUD_SETTINGS_INVALID` rather than an
empty list. A ledger path of the wrong type, a directory that cannot be listed, a
run record with no history or a last transition that names another state, a
`torn_down` record with no settled amount, a time without a zone and a negative or
non-finite amount all count as unreadable, so `bh cloud status` now fails with an
error on a damaged ledger instead of printing an empty report. It builds no provider client and reads no token. See the
[dashboard](dashboard.md#cloud-runs) for the screen and its view-only serving mode.

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
the original absolute deadline and the spend reservation. The controller
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
