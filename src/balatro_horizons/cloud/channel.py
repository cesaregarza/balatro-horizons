"""The worker channel over SSH: each tick reads the running worker's status file.

A read that fails or finds nothing is no news, so the heartbeat grace applies. A status
reports how many moves have committed, that play finished, or the stage that failed.
"""

from .provider import WorkerReport


class SshChannel:
    def __init__(self, ledger, link):
        self.ledger = ledger
        self.link = link

    def report(self, run_id, timeout):
        read = self.link.read_status(self.ledger.get(run_id), timeout=timeout)
        if read.malformed:
            return WorkerReport(failure="worker_status_malformed")
        status = read.status
        if status is None:
            return WorkerReport()
        if status.state == "failed":
            return WorkerReport(failure=f"worker_failed:{status.stage}", status=status)
        return WorkerReport(actions=status.actions, finished=status.state == "finished",
                            status=status)
