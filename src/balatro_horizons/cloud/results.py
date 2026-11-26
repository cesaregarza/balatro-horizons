"""Copying a finished game's results to the controller host, in the tick that ends the run.

The worker writes its results file before its status says finished. The controller reads
it with one SSH call, capped at 1 MiB and at the time left before the run deadline, and
keeps it under the ledger's `results/` directory. The run names how the copy went, and
its teardown follows either way, so a failed copy never delays it.
"""

import json
from pathlib import Path

from .durable import durable_json
from .install import CALL_SECONDS, WORKER_DIR

RESULTS_BYTES = 1 << 20
# One byte past the cap shows the file is too large without reading the rest. A missing
# file reads as nothing.
RESULTS_COMMAND = f"head -c {RESULTS_BYTES + 1} {WORKER_DIR}/results.json 2>/dev/null || true"


def results_path(ledger_root, run_id):
    return Path(ledger_root) / "results" / f"{run_id}.json"


def copy_results(link, record, ledger_root, clock):
    """Copy the run's results, owner-only; "copied", or the name of what went wrong."""
    left = (record.deadline - clock()).total_seconds()
    if left <= 0:
        return "no_time_left"
    reply = link.call(record, RESULTS_COMMAND, timeout=min(CALL_SECONDS, left))
    if reply.outcome == "timed_out" and clock() >= record.deadline:
        # Cut off by its cap, the copy is named by the deadline.
        return "no_time_left"
    if not reply.ok:
        return f"ssh_{reply.outcome}"
    if not reply.stdout:
        return "results_missing"
    if len(reply.stdout) > RESULTS_BYTES:
        return "results_too_large"
    try:
        results = json.loads(reply.stdout)
    except ValueError:
        return "results_malformed"
    if not isinstance(results, dict):
        return "results_malformed"
    durable_json(results_path(ledger_root, record.run_id), results, private=True)
    return "copied"
