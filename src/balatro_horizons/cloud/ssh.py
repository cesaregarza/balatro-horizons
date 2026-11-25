"""SSH to a run's Droplet through the system OpenSSH client, with one fixed set of options.

Every connection checks the host key strictly against the run's pin and offers only the
controller's registered key. OpenSSH sends a command, or its stdin, only after both the
host key and the login have verified, so a mismatch sends nothing.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

CONNECT_SECONDS = 10
ALIVE_SECONDS = 10
ALIVE_COUNT = 3
# OpenSSH exits 255 for its own errors. The remote commands sent here never do.
SSH_ERROR = 255
MISMATCH_SIGNS = (
    "Host key verification failed",
    "REMOTE HOST IDENTIFICATION HAS CHANGED",
    "no matching host key type found",
)

Outcome = Literal["ok", "unreachable", "host_key_mismatch", "failed", "timed_out"]
# Both come before the pinned key is served, so both wait for the boot timeout.
RETRIED = ("unreachable", "host_key_mismatch")


class Transport(Protocol):
    """Runs one OpenSSH client program; tests substitute a fake."""

    def run(self, argv: list[str], *, stdin: bytes | Path | None = None,
            timeout: float) -> subprocess.CompletedProcess: ...


class OpenSsh:
    def run(self, argv, *, stdin=None, timeout):
        """Run with a timeout; stdin streams from a file, so a large push is never in memory."""
        if isinstance(stdin, Path):
            with stdin.open("rb") as stream:
                return subprocess.run(argv, stdin=stream, capture_output=True,
                                      timeout=timeout, check=False)
        if stdin is None:
            return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=timeout, check=False)
        return subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout,
                              check=False)


@dataclass(frozen=True)
class Reply:
    outcome: Outcome
    stdout: bytes = b""

    @property
    def ok(self):
        return self.outcome == "ok"


def ssh_options(key_path, known_hosts):
    """No config file, the pinned host key only, the access key only, and bounded waits."""
    return [
        "-F", "none",
        "-i", str(key_path),
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", f'UserKnownHostsFile="{known_hosts}"',
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "HostKeyAlgorithms=ssh-ed25519",
        "-o", "UpdateHostKeys=no",
        "-o", "CheckHostIP=no",
        "-o", f"ConnectTimeout={CONNECT_SECONDS}",
        "-o", f"ServerAliveInterval={ALIVE_SECONDS}",
        "-o", f"ServerAliveCountMax={ALIVE_COUNT}",
    ]


def ssh(transport, key_path, known_hosts, address, command, *, stdin=None, timeout):
    """Run one remote command as root; the address follows `--`, so it is never an option."""
    argv = ["ssh", *ssh_options(key_path, known_hosts), "--", f"root@{address}", command]
    try:
        completed = transport.run(argv, stdin=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Reply("timed_out")
    return Reply(classify(completed), completed.stdout)


def classify(completed):
    if completed.returncode == 0:
        return "ok"
    if completed.returncode != SSH_ERROR:
        return "failed"
    stderr = completed.stderr.decode(errors="replace")
    return "host_key_mismatch" if any(sign in stderr for sign in MISMATCH_SIGNS) else "unreachable"
