"""The worker channel over SSH: each tick reads the running worker's status file.

In this slice a run is done once its game is ready, which makes the run a boot check.
Anything else, including a read that fails, is no news, so the heartbeat grace applies.
"""

from .provider import WorkerReport


class SshChannel:
    def __init__(self, ledger, link, clock):
        self.ledger = ledger
        self.link = link
        self.clock = clock

    def report(self, run_id):
        read = self.link.read_status(self.ledger.get(run_id))
        if read.status is None or read.status.state != "ready":
            return WorkerReport(last_seen=None, finished=False)
        return WorkerReport(last_seen=self.clock(), finished=True)
