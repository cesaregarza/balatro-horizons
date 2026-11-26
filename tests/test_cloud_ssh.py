"""The SSH transport's fixed options and outcome classes; no connection is ever made."""

import subprocess
from pathlib import Path

import pytest

from balatro_horizons.cloud.ssh import OpenSsh, Reply, classify, ssh, ssh_options

KEY = Path("/stand-in/access_key")
PIN = Path("/stand-in/known_hosts/r1")


class Recorder:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, argv, *, stdin=None, timeout):
        self.calls.append((argv, stdin, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def finished(code, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(["ssh"], code, stdout, stderr)


def test_options_pin_the_host_key_offer_only_the_access_key_and_bound_every_wait():
    assert ssh_options(KEY, PIN) == [
        "-F", "none",
        "-i", "/stand-in/access_key",
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", 'UserKnownHostsFile="/stand-in/known_hosts/r1"',
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "HostKeyAlgorithms=ssh-ed25519",
        "-o", "UpdateHostKeys=no",
        "-o", "CheckHostIP=no",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=3",
    ]


def test_each_call_runs_one_command_as_root_after_the_options_end():
    transport = Recorder(finished(0, b"answer"))
    reply = ssh(transport, KEY, PIN, "198.51.100.7", "true", stdin=b"input", timeout=42)
    assert reply == Reply("ok", b"answer") and reply.ok
    argv, stdin, timeout = transport.calls[0]
    assert argv == ["ssh", *ssh_options(KEY, PIN), "--", "root@198.51.100.7", "true"]
    assert (stdin, timeout) == (b"input", 42)


@pytest.mark.parametrize("code,stderr,outcome", [
    (0, b"", "ok"),
    (1, b"", "failed"),
    (2, b"Connection refused", "failed"),
    (255, b"ssh: connect to host 192.0.2.2 port 22: Connection timed out", "unreachable"),
    (255, b"root@192.0.2.2: Permission denied (publickey).", "unreachable"),
    (255, b"Host key verification failed.", "host_key_mismatch"),
    (255, b"@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @", "host_key_mismatch"),
    (255, b"Unable to negotiate: no matching host key type found.", "host_key_mismatch"),
])
def test_outcomes_are_classified_from_the_exit_status_and_openssh_messages(code, stderr,
                                                                           outcome):
    assert classify(finished(code, stderr=stderr)) == outcome


def test_a_call_past_its_timeout_is_named():
    transport = Recorder(subprocess.TimeoutExpired(["ssh"], 1))
    assert ssh(transport, KEY, PIN, "198.51.100.7", "true", timeout=1) == Reply("timed_out")


def test_the_system_client_streams_files_and_bounds_every_run(tmp_path):
    client = OpenSsh()
    assert client.run(["cat"], stdin=b"bytes in", timeout=10).stdout == b"bytes in"
    pushed = tmp_path / "bundle"
    pushed.write_bytes(b"file in")
    assert client.run(["cat"], stdin=pushed, timeout=10).stdout == b"file in"
    assert client.run(["cat"], timeout=10).stdout == b""
    with pytest.raises(subprocess.TimeoutExpired):
        client.run(["sleep", "5"], timeout=0.1)
