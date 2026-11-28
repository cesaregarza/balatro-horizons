# Cloud runner

The cloud runner runs each game on a throwaway DigitalOcean Droplet, one per run,
and guarantees the Droplet is deleted and verified gone afterwards, including
after an error, a crash or a lost worker. In this slice the controller checks its
access, creates the Droplet with a host key it pinned in advance, and installs and
starts the game over SSH. The worker plays one scripted game, with no model, and the
controller copies its results back before it tears the Droplet down.

The laptop and desktop native path is unchanged. The package lives in
`src/balatro_horizons/cloud/`, outside the execution manifest, so restore
compatibility is untouched.

## Commands

| Command | Provider calls | Purpose |
| --- | --- | --- |
| `bh cloud status` | none | Ledger, launch gate, budget, the last inventory read and active alerts. Exits 1 while an alert is active. |
| `bh cloud launch RUN_ID --allow-provider-calls [--access-config FILE]` | yes | Start the controller, re-evaluate existing runs and the owned inventory, run the preflight, launch one run and supervise until every run is torn down. Exits nonzero if any alert is active at the end. |
| `bh cloud cancel RUN_ID [--allow-provider-calls]` | only with the flag | Without the flag, record the request for the controller. With it, also tear down now. |
| `bh cloud reconcile --allow-provider-calls` | yes | Tear down expired runs and orphans, look for the Droplets of unresolved creates, and verify each. Safe from a timer while the controller is down. |

Every command that can reach DigitalOcean refuses with
`PROVIDER_CALLS_NOT_ALLOWED` unless `--allow-provider-calls` is given, as
`--allow-paid` does for model calls. The token is read from `DIGITALOCEAN_TOKEN`
when the client is built and is held only in the HTTP client's headers. It is
never logged, written to the ledger, sent as user data or printed.

Settings live in `configs/cloud.yaml`, not `config.py`; pass another file with
`--cloud-config`. The ledger lives under `--data-dir`, in `cloud/`.

## Access settings

Operations owns the controller's SSH key, the firewall and the game bundle, and
sets up the key and the firewall once; no run creates either. Their values live in
`private/cloud-access.yaml`, which git ignores, or in the file `--access-config`
names. Only `launch` reads them, so `status`, `cancel` and `reconcile`, and with
them every teardown path, work without the file. A missing file refuses the launch
with `CLOUD_ACCESS_MISSING`, and an unreadable or invalid one, including one with an
unknown field, with `CLOUD_ACCESS_INVALID`.

| Field | Meaning |
| --- | --- |
| `ssh_key_path` | The absolute path of the private key the controller logs in with. |
| `ssh_key_fingerprint` | That key's fingerprint as DigitalOcean records it: colon-separated MD5 hex. |
| `firewall_id` | The firewall that covers the owner tag. |
| `bundle.path` | The absolute path of the game bundle on the controller host. |
| `bundle.name`, `bundle.bytes`, `bundle.sha256` | The bundle's descriptor. |

## Run lifecycle

1. **Preflight.** Once the launch gate admits a launch, the controller reads the
   provider and the bundle, and refuses with the codes listed below. The preflight
   creates nothing and runs before the run is recorded, so a refusal leaves no
   record. It is the one exception to persisting a run before any provider call.
2. **Persist first.** The run and its absolute UTC deadline
   (`now + max_run_hours`) are written before the create.
3. **Create.** One Droplet is created with two tags, the owner tag
   `bh-owner:<controller_id>` and the run tag `bh-run:<run_id>`, the registered SSH
   key, and user data that installs the run's host key. See
   [SSH and the host key](#ssh-and-the-host-key).
4. **Install.** Once the Droplet is `active` and has a public IPv4 address, the
   controller connects over SSH, pushes the worker program, its driver and its
   pins, pushes the bundle, and starts the worker detached with `systemd-run`, so
   no tick waits on the install. Each later tick reads the worker's status file
   over SSH.
5. **Play.** The run stays `provisioning` until the worker reports that play has
   started, or that it has already finished, then moves to `running`. A ready game
   is not enough.
6. **Supervise.** Each tick re-reads the ledger, reads the worker's status, and
   tears the run down on the first reason that applies. See
   [Progress and the heartbeat](#progress-and-the-heartbeat).
7. **Results.** Once a read finds the game finished, the controller copies the
   results back, then tears the run down as `succeeded` in the same tick. See
   [The results copy](#the-results-copy).
8. **Tear down and settle.** Verified teardown settles the elapsed time against
   the infrastructure budget. The run record keeps the worker's latest report.

The preflight reports every check that fails, in this order:

| Code | Refused when |
| --- | --- |
| `SSH_KEY_NOT_REGISTERED` | The account has no key with `ssh_key_fingerprint`. |
| `FIREWALL_NOT_RESTRICTED` | The firewall is missing, does not target the owner tag, or does not admit SSH from at least one single address, and nothing else. It needs at least one inbound rule. Each rule must be TCP port 22 with at least one source, and each source must be a single IPv4 address (/32), never a tag, Droplet or load balancer. Outbound rules are not checked, because the worker needs HTTPS and DNS out. |
| `SIZE_NOT_OFFERED` | The size is unavailable, or not offered in the region. |
| `PRICE_UNDERSTATED` | The size's hourly price is higher than `hourly_price_usd`, the price the budget reserves at. |
| `BUNDLE_MISMATCH` | The bundle's file name, size or SHA256 differs from its descriptor. The size is checked first, then the SHA256, read in chunks. |

A provider read that fails or is malformed refuses with `PREFLIGHT_UNVERIFIED`
alone, since nothing was checked. A run's host key that cannot be made or pinned
refuses with `HOST_KEY_UNAVAILABLE`, also before anything is recorded.

### The worker's stages

The worker is a standard-library Python program in `cloud/worker/`, copied to the
Droplet with its driver, `driver.py`, and with `pins.toml`, which pins each download
by URL, size and SHA256. It runs under `python3 -I`, which keeps its own directory
off the import path, so it loads the driver by its path, as it reads the pins; a
driver that cannot load fails the `packages` stage before anything is installed.
Before each stage starts, the worker writes the stage to its status file, renamed
into place so a reader never sees half of it. A failure writes `failed` with the
stage, a short reason, and capped tails of the worker's log and of the display's
and the game's stderr. The worker then exits.

1. `packages`: wait for cloud-init and record its final status, which shows
   whether the user data's command failed. Then add the i386 architecture and
   install the apt package list in `pins.toml`.
2. `dependencies`: download GE-Proton and the two UMU packages and check each
   one's size and SHA256. Only then install the packages and unpack the runner.
3. `bundle`: check the pushed bundle's size and SHA256 against the descriptor
   values the controller passes, then unpack it. The bundle is a tar archive whose
   top level holds the game's executable and its mod directory. A bundle that
   nests them in a directory, such as `game/`, fails this stage.
4. `user`: create the unprivileged account if it does not exist yet, and move the
   game and the runner into its home.
5. `display`: start Xvfb in its own transient unit, check within a bound that
   display `:99` answers while Xvfb is still running, and record `glxinfo -B`.
6. `game`: start the game as that account in a transient unit, with only the
   launch recipe's environment, `CPUQuota=200%`, `MemoryMax=6656M`, `UMask=0077`,
   and `RuntimeMaxSec` set to the time left before the run's deadline. Past the
   deadline, nothing starts.
7. `ready`: an authenticated `bh_inspect` on 127.0.0.1:12346 reports the menu
   within 180 seconds.
8. `play`: the driver plays one game, described below. The status file is written
   again after each committed move. Play ends at a game over, a win or the action
   cap, and the worker then writes its results and the `finished` status. Any
   other end fails the stage with a named reason. Results that cannot be built
   or written fail it too, with the reason `results not written: <code>`, where
   the code is the errno name, such as `ENOSPC`, or else the error's type.

The game's token and instance ID are made on the Droplet and never leave it. They
are taken out of the log tails, and they reach the game through an owner-only
environment file, never a command line. On the way out the worker records what it
observed, because the apt packages and steamrt4 are not pinned: the kernel, a
digest of the installed package list, the steamrt4 build, cloud-init's final
status, the renderer, the game's version and the game's seed. Each is capped in
size.

### The driver

The driver, `cloud/worker/driver.py`, uses only the standard library and runs in the
worker's process once the game is ready. It talks to the game's local API with
JSON-RPC 2.0. Every request carries the run's token among its parameters and a
fresh random ID, and a reply counts only if it carries that ID; an `error` reply is
a refusal.

- **The game.** Deck `RED`, stake `WHITE`, and a fresh eight-character seed from
  the run API's alphabet, recorded before play starts.
- **Between moves.** The driver inspects every 0.25 seconds, for at most 15
  seconds, until the game reports itself ready and not busy. An inspect that fails
  counts as not ready. Otherwise play fails with `not ready between moves`, or with
  `game exited` once the game's unit has stopped.
- **The end.** After each inspect, a win ends play as `won` and `GAME_OVER` as
  `game_over`. Once 200 moves have committed, play ends as `action_cap`. Every call
  but an inspect or a request-status counts as a move, `start` included.
- **A lost answer.** When a move's answer is lost or does not answer the call, the
  driver asks `bh_request_status` by the move's own ID. The game settles every move
  it admits within its own bound of at most 60 seconds, so a `pending` status is
  asked about again every 2 seconds, and no ask starts once 85 seconds have passed
  since the send. Only `committed` lets play go on, with the stored response as the
  move's result. A move is never sent twice. Any other status, a status call that
  fails, or a move still pending when its window ends, ends play with
  `uncertain move: <move>; answer <kind> after <n> s; status <value>`, or with
  `status lookup <kind>` in place of `status <value>` when the status call failed.
  - `<move>` is the method, or `bh_action <action>`, where `<action>` is the action
    when it is 1 to 20 lowercase letters and underscores, and `other` otherwise.
  - `<kind>` is how the answer was lost: the class of the transport's error, such
    as `TimeoutError` or `RemoteDisconnected`, or `HTTPError <code>` for an HTTP
    error; `reply id mismatch` or `no result` for a reply that does not answer the
    call; and `error reply` for a status call the game refused. Anything else reads
    `other`.
  - `<n>` is the whole seconds from the move's send to its lost answer.
  - `<value>` is `pending` or `rejected`; `unknown` when the game admitted the move
    and gave up on it, so its record keeps the move's intent; `unseen` when the game
    has no record of it; or `invalid` for a status the driver cannot read.
  - When the asking ends and the game's unit is no longer running, the reason
    begins `game exited during <move>` instead, with the same detail after it.

  Every reason fits in 200 characters.
- **A refusal.** An error reply to a move ends play with
  `move refused: <move>: <code>`, where `<code>` is the error's message when it is
  1 to 40 capital letters and underscores, and `other` otherwise. Nothing is
  retried.
- **Timeouts.** 10 seconds for an inspect, 12 for a request-status and 85 for any
  other call.
- **The policy.** The pilot's scripted choices, from the public observation only,
  never the seed or the undealt cards. It selects each blind and cashes out each
  round. It plays the best-scoring hand, discarding the rest first once a game and
  again while the hand is weak. In the shop it buys a joker there is room for,
  uses one planet, opens one pack and rerolls once if money allows, then moves on.
  In an open pack it takes the first card it can use, or skips the pack. It
  exercises the game; it does not try to play it well.

### The status file

| Field | Meaning |
| --- | --- |
| `stage` | The current stage, from `packages` to `play`. |
| `state` | `running` until the worker ends, then `finished` once play ended normally, or `failed`. Only records kept from before play existed carry `ready`. |
| `reason` | A failure's short reason. |
| `actions` | Committed moves so far. |
| `outcome` | Once finished: `game_over`, `won` or `action_cap`. |
| `started_at`, `finished_at` | When each stage started, and when the worker ended. |
| `observed` | What the run observed, listed above, including the seed. |
| `observed.game_unit` | After a failure in the `game`, `ready` or `play` stage only: how the game's unit stood, in one line of closed values and numbers of at most 200 characters. It starts `running=yes`, `no` or `unavailable`. A running unit adds `memory.peak` and `memory.current`, then the `oom`, `oom_kill` and `max` counts from `memory.events`, read from the unit's cgroup through its `ControlGroup` property. Otherwise it adds `main=<how>/<status>`, where `<how>` is `exited`, `killed` or `dumped`, and the unit's `result`, such as `exit-code`, `signal`, `oom-kill` or `success`, both from the unit's journal records, since a transient unit is unloaded once it stops. Last, `journal_oom=yes` or `no` says whether the journal records an out-of-memory kill for the unit. A value that cannot be read or parsed is `unavailable`, and no journal or cgroup text is kept. |
| `evidence` | On failure only: the capped log tails. |

### Progress and the heartbeat

Only the controller's clock decides. A time the worker writes never does, so the
worker's report carries none: it is no news, a committed-move count, a finished
game with its count, or a named failure. A read that fails or finds nothing is no
news. The run record keeps the highest count a read has shown, and only a higher
count restarts the heartbeat grace, from when that read returned. A read that
succeeds with the same count is not progress.

Each tick of a running run, in order:

1. The stop check: a cancel, then the run deadline.
2. The status read, which may take 60 seconds or the time left before the run
   deadline, whichever is less.
3. A fresh clock reading when the read returns, then the stop check again. A
   deadline that has passed by then wins over a finished report.
4. A failed stage, or a status that does not parse, tears the run down at once.
5. A finished game: the results copy, then teardown as `succeeded`, in the same
   tick.
6. A higher count is recorded, and the grace restarts.
7. The heartbeat check: more than `heartbeat_grace_seconds` since the grace last
   restarted ends the run as `heartbeat_lost`.

One function decides the stop reasons for every caller, the controller and the
reconciler alike: a cancel, then the run deadline, then the boot deadline while the
run provisions.

### The results copy

At the end of play, once a move has committed, the worker writes `results.json` in
its directory, owner-only and atomically, before its final status, `finished` or
`failed`. It holds the outcome, which is `null` after a failed play, and the count,
the ante and round reached, the seed, deck and stake, each committed move with its
method, its action if it has one and the state it left the game in, the stage
start times and the observations. It never holds the token, the instance ID or a
request ID. A failed play whose results cannot be written keeps its own reason,
and the copy then finds no file.

When a read finds the game finished, or finds that play failed, the controller
reads the file with one SSH call. The call may take 60 seconds or the time left
before the run deadline, whichever is less, and reads at most 1 MiB. The file must
parse as a JSON object. The controller writes it owner-only to
`cloud/results/<run_id>.json`, through the same durable write as the ledger. The
run record's `results_copy`, which `bh cloud status` shows, names how it went:

| `results_copy` | Meaning |
| --- | --- |
| `copied` | The results are on the controller host. |
| `ssh_<outcome>` | The call failed: `ssh_failed`, `ssh_unreachable`, `ssh_host_key_mismatch` or `ssh_timed_out`. |
| `results_missing` | The file was not there, or was empty. |
| `results_too_large` | The file is larger than 1 MiB. |
| `results_malformed` | The file is not a JSON object, or is nested too deeply to parse. |
| `results_unwritable` | The controller could not write the copy, as on a full disk. |
| `no_time_left` | The run deadline cut the copy short. |

Teardown follows in the same tick whatever the copy's outcome, and a failed copy
never delays it. The status read decides the teardown reason: a finished report
read before the run deadline means `succeeded`, and a copy cut short is named only
in `results_copy`. A failed play read before the run deadline, while the run boots
or once it runs, is copied the same way and torn down as `worker_failed:play`. A
failure in an earlier stage, or a cancelled, expired or heartbeat-lost run, is torn
down with no copy, and its kept status holds the evidence tails as before.

### Teardown reasons

| Trigger | Teardown reason |
| --- | --- |
| A read before the run deadline finds the game finished; the results copy runs first | `succeeded` |
| Play not started within `boot_timeout_seconds`, or the Droplet gone during boot | `boot_failed` |
| Play not started within `boot_timeout_seconds`, and the last SSH attempt met a host key other than the pinned one | `host_key_mismatch` |
| The active Droplet has no public IPv4 address | `no_public_ipv4` |
| The worker reports a failed stage, `play` included | `worker_failed:<stage>` |
| During boot, an SSH call fails, or it times out with time still left before the boot and run deadlines. The step is `probe`, `push_worker`, `push_bundle`, `start` or `status`. A call that times out with no time left is named by the deadline instead: `deadline`, `boot_failed` or `host_key_mismatch`. The bundle push may take all the time left, so it never gets `ssh_timed_out` | `ssh_failed:<step>` or `ssh_timed_out:<step>` |
| The worker's status file does not parse, during boot or play | `worker_status_malformed` |
| The create call is rejected with a 4xx, or an ambiguous create resolves | `create_failed` |
| The absolute deadline passes | `deadline` |
| A cancel request | `cancelled` |
| No higher committed-move count within `heartbeat_grace_seconds`, whether or not the reads succeed | `heartbeat_lost` |
| Any error or interrupt in the supervising loop | `controller_error` |
| A run still provisioning when a controller starts | `restart_while_provisioning` |
| An owner-tagged Droplet with no live ledger run | `orphan` |

`boot_timeout_seconds` covers everything from the create until play starts,
including the install. It is 1200 until a live run measures the real time.

On a controller restart, every live run is re-evaluated. A provisioning run,
including one whose worker is still installing, is torn down, never adopted; one
with no recorded Droplet ID is held as an unresolved create, because the crash may
have followed an accepted create. A running run is torn down if it is past its
original deadline; otherwise it gets a fresh heartbeat grace window and is torn
down if no progress arrives. An interrupted teardown resumes. Only one controller
can run at a time: it holds `controller.lock` for its lifetime, and a second one
exits with `CONTROLLER_BUSY`.

At startup, and again just before admitting each launch, the controller reads the
whole owner-tag inventory, tears down orphans and records unmatched Droplets. If
that read fails or is malformed, the launch is refused with
`INVENTORY_UNVERIFIED`, and `status` shows the failed read.

## SSH and the host key

For each run, the controller makes a fresh ed25519 host key with `ssh-keygen`, in
an owner-only temporary directory that is gone before the create is sent. It pins
the public half in an owner-only known_hosts file for the run, in the ledger's
`known_hosts/` directory. The user data is a cloud-config that installs the pair as
the Droplet's only host key and runs one command, `systemctl try-restart
ssh.service`, with nothing that hides its failure: an sshd that started before the
key was installed then serves it, and an sshd that is not running is left alone.
The user data carries nothing else: no login hardening, no sshd configuration, no
token and no game files. A command that succeeds proves nothing on its own; the
controller trusts the Droplet only once the pinned host key and the login both
verify.

Every SSH call uses the system OpenSSH client with one fixed set of options: no
configuration file, only the access key, batch mode, strict checking against the
run's known_hosts file with no global file, ed25519 host keys only, and bounded
connect and keepalive waits. Every call also has a subprocess timeout. OpenSSH runs
a command and sends its input only after both the host key and the login verify.
An unreachable host, a refused login and a host key other than the pinned one are
retried until the boot timeout, because sshd may answer before cloud-init installs
the key. The pin is never replaced, accepted anew or deleted. Any other failed or
timed-out call ends the boot with a named reason.

The Droplet's public IPv4 address is kept in the run record for SSH only; `status`
and the dashboard do not show it.

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

The controller believes a Droplet is gone only on evidence that it was deleted.
DigitalOcean documents no read-after-write guarantee, so the controller relies on
none: a by-ID read and a listing can each lag or disagree. An ID counts as gone
only when both of these hold, in order:

1. It was deleted, shown in one of two ways:
   - A fresh by-ID read showed it with both tags, and the provider acknowledged our
     DELETE with a 2xx. A 404 on DELETE is not an acknowledgment. The
     acknowledgment is recorded durably on the run's resource before any later
     read is trusted.
   - The account action log shows a completed `destroy` action for that ID that
     started no earlier than the run's creation, less the five-minute clock-skew
     allowance. One that failed, is still in progress, names another ID or started
     earlier proves nothing.
2. Afterwards, a by-ID read returns 404 and the run-tag listing omits the ID.

Each DELETE attempt is recorded durably on the resource before the request is
sent. When every recorded ID's DELETE is acknowledged, a pass goes straight from
the deletes to the two absence checks and settles without reading the action log,
so the verified time and the settled cost never include a read of it. An ID with
no acknowledgment can clear only on the action log, so for it the pass reads the
log on the way, within the time left before the pass gives up: the cleanup window,
or a fresh `cleanup_verify_seconds` window for the single pass that retries a
`cleanup_unverified` run. A read cut off at that time, or one that returns after
it, finds nothing. A 404 alone, a read that failed and a read that ran out of time
never clear the ID, and when the window ends the run is marked `cleanup_unverified`.

Once a run has settled, however it settled, the controller looks its IDs up in the
action log once, so each run's record shows whether the action-log path would also
have cleared it. One 60-second deadline covers the whole lookup: every page,
attempt and wait. Each ID records when the lookup ended, how it ended (`found`,
`not_found`, `failed` or `timed_out`) and the best destroy action it found. A
completed destroy, once recorded, is never replaced, because a later read may no
longer list it. The lookup never changes the settled run: its state, verified
time, settled cost and reservation stay as settling left them. It is not retried,
so a crash between settling and the lookup leaves a settled run with no lookup
recorded.

Before either kind of evidence, a 404 or an empty listing proves nothing. The run
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
alert for it, and new launches are refused. With no DELETE of ours behind them, no
read clears them: not a 404, not absence from a listing, and not a destroy action
in the log. A run that holds such an ID never settles, so it keeps its reservation
and its slot. Delete the Droplet by hand after checking what it is.

### Cases that hold

Two cases now clear on the action log, as described above, and still need the 404
and the listing check that follow:

- A DELETE whose acknowledgment was lost, for example to a crash before it was
  recorded.
- A Droplet deleted outside the controller, which reads 404 with no
  acknowledgment.

These still hold launches, and the run's reservation where there is one, until an
operator path is designed in a later slice. Each needs an evidence-backed recovery
path, with no force-clear:

- A create the provider never shows.
- An unmatched Droplet.
- An ID the controller may not delete.

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
`settled_usd`, and `results_copy`, which is unset until a copy is tried. The report
also carries `read_at`. The dashboard reads the ledger
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
30 seconds and then exits with `LEDGER_BUSY`. Each run's pinned host key is in
`cloud/known_hosts/`, in an owner-only file named after the run, and each copied
result is in `cloud/results/`, likewise.

## Limits

- The driver and its policy have never played a real game. The shapes of its
  requests and replies, the request-status record and the readiness fields follow
  the pilot's record, and the first live run is their first real check.
- The host-key bootstrap has never run on a real Droplet; the pilot set its
  Droplet up by hand. The first live run is its first real check.
- The shapes of the destroy action, the Droplet's network fields and the size
  listing follow the API documentation. The first live run records the real ones,
  and the action-log path is unproven until it shows a destroy in the expected
  shape.
- Xvfb runs in its own unit, outside the game's memory limit.
- Only Droplets are created. The ledger records each resource's kind so firewalls
  and SSH keys can join later.
- All tests use a fake provider or a fake HTTP transport, a fake SSH transport, a
  fake machine for the worker, and an in-process fake of the game's local API for
  the driver. No real provider call, SSH connection, worker run or game has been
  made.
