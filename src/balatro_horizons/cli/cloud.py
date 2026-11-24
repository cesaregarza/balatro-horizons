"""bh cloud: launch, status, cancel and reconcile throwaway Droplet workers."""

import argparse
import json
from pathlib import Path

from balatro_horizons.cloud.controller import Controller, utc_now
from balatro_horizons.cloud.digitalocean import client_from_environment
from balatro_horizons.cloud.gate import blockers, launch_refusals, remaining_budget_usd
from balatro_horizons.cloud.ledger import Ledger, LedgerBusy
from balatro_horizons.cloud.reconcile import reconcile
from balatro_horizons.cloud.settings import DEFAULT_PATH, load_settings

PROVIDER_FLAG_HELP = "Required for any call that can reach DigitalOcean and cost money"


def configure_parser(parser):
    parser.description = (
        "Run each game on a throwaway Droplet that is always deleted and verified gone. "
        "Only status works without --allow-provider-calls; the token is read from "
        "DIGITALOCEAN_TOKEN and is never printed or stored."
    )
    operations = parser.add_subparsers(dest="cloud_operation", required=True)
    launch = operations.add_parser("launch", help="Start the controller and launch one run")
    launch.add_argument("run_id")
    operations.add_parser("status", help="Ledger, launch gate and alerts; no provider call")
    cancel = operations.add_parser("cancel", help="Request cancellation; tear down now with the flag")
    cancel.add_argument("run_id")
    operations.add_parser("reconcile", help="Tear down expired runs and orphans, then verify")
    for name, sub in operations.choices.items():
        sub.add_argument("--cloud-config", type=Path, default=DEFAULT_PATH)
        sub.add_argument("--data-dir", type=Path, default=argparse.SUPPRESS)
        if name != "status":
            sub.add_argument("--allow-provider-calls", action="store_true",
                             help=PROVIDER_FLAG_HELP)
        sub.set_defaults(operation_handler=run, operation_parser=sub)


def run(args):
    try:
        settings = load_settings(args.cloud_config)
        ledger = Ledger(args.data_dir / "cloud")
        operation = args.cloud_operation
        if operation == "status":
            return show_status(ledger, settings)
        if operation == "cancel" and not args.allow_provider_calls:
            # Recording the request needs no provider; the controller acts on it.
            return emit(Controller(ledger, None, settings).cancel(args.run_id))
        if not args.allow_provider_calls:
            return emit({"error": "PROVIDER_CALLS_NOT_ALLOWED"}, 2)
        return OPERATIONS[operation](args, ledger, settings, client_from_environment())
    except (ValueError, OSError, LedgerBusy) as error:
        code = str(error)
        return emit({"error": code if code.isupper() and len(code) < 100 else type(error).__name__}, 1)


def launch_command(args, ledger, settings, provider):
    controller = Controller(ledger, provider, settings)
    with ledger.controller_lock():
        controller.recover()
        result = controller.launch(args.run_id)
        print(json.dumps(result, sort_keys=True), flush=True)
        controller.supervise()
    # Any cleanup left unverified, or any other active alert, makes the launch a failure.
    status_code = show_status(ledger, settings)
    return status_code or (0 if result["outcome"] in ("launched", "duplicate") else 1)


def cancel_command(args, ledger, settings, provider):
    return emit(Controller(ledger, provider, settings).cancel(args.run_id, now=True))


def reconcile_command(args, ledger, settings, provider):
    result = reconcile(ledger, provider, settings)
    unverified = [run for run in result["runs"] + result["orphans"]
                  if run["state"] in ("cleanup_unverified", "create_unresolved")]
    present = [item for item in result["unmatched"] if item["state"] == "present"]
    failed = unverified or present or result["orphan_scan"] != "COMPLETE"
    return emit(result, 1 if failed else 0)


OPERATIONS = {"launch": launch_command, "cancel": cancel_command,
              "reconcile": reconcile_command}


def status_report(ledger, settings, now):
    runs, inventory = ledger.runs(), ledger.inventory()
    refusals = launch_refusals(ledger, settings, now)
    return {
        "launches": "REFUSED" if refusals else "ALLOWED",
        "refusals": refusals,
        "alerts": active_alerts(ledger),
        "inventory": None if inventory is None else {
            "complete": inventory.complete, "checked_at": inventory.at.isoformat()},
        "budget_usd": {
            "total": settings.infrastructure_budget_usd,
            "remaining": round(remaining_budget_usd(runs, settings, now), 6),
            "worst_case_per_run": round(settings.worst_case_usd, 6),
        },
        "runs": [{
            "run_id": record.run_id, "state": record.state,
            "deadline": record.deadline.isoformat(), "teardown_reason": record.teardown_reason,
            "settled_usd": record.settled_usd,
        } for record in runs],
    }


ALERT_MESSAGES = {
    "CLEANUP_UNVERIFIED": "Cleanup of run {run_id} is unverified. New launches are refused "
                          "until a later sweep verifies its Droplets are gone.",
    "CREATE_UNRESOLVED": "The create for run {run_id} may have succeeded. New launches are "
                         "refused until its Droplet is found and its deletion verified; "
                         "empty reads never clear it.",
    "UNMATCHED_DROPLET": "Droplet {droplet_id} is one this controller may not delete: it lost "
                         "its run's tags, or carries the owner tag with no usable run tag. New "
                         "launches are refused, and no read clears it.",
    "INVENTORY_UNVERIFIED": "The last read of the owned inventory failed. New launches are "
                            "refused until a complete read.",
}


def active_alerts(ledger):
    """One alert per durable blocker, from the same records the launch gate reads."""
    return [blocker | {"since": blocker["since"].isoformat(),
                       "message": ALERT_MESSAGES[blocker["code"]].format(**blocker)}
            for blocker in blockers(ledger)]


def show_status(ledger, settings):
    report = status_report(ledger, settings, utc_now())
    return emit(report, 1 if report["alerts"] else 0)


def emit(payload, code=0):
    print(json.dumps(payload, indent=2, sort_keys=True))
    return code
