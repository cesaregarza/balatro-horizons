"""The run's SSH host key: made on the controller, pinned there, installed by user_data.

The Droplet's first SSH answer is checked against this pin, so it is verified rather
than trusted on first use. The pin is written once, before the create, and no SSH call
replaces or removes it.
"""

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

KEYGEN_SECONDS = 30
PUBLIC_KEY = re.compile(r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}")
# The only command user_data runs. It makes a daemon that already started serve the pinned
# key, and does nothing when the service is not running, as under socket activation.
RESTART_SSH = ["systemctl", "try-restart", "ssh.service"]


class HostKeyError(ValueError):
    """No usable host key could be made; the message is the refusal code."""


@dataclass(frozen=True)
class HostKey:
    private: str
    public: str


def generate_host_key(transport):
    """A fresh ed25519 pair. Its owner-only directory, private half included, is gone on return."""
    with tempfile.TemporaryDirectory(prefix="bh-host-key-") as directory:
        path = Path(directory) / "host_key"
        argv = ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(path)]
        try:
            completed = transport.run(argv, timeout=KEYGEN_SECONDS)
            if completed.returncode != 0:
                raise HostKeyError("HOST_KEY_UNAVAILABLE")
            key = HostKey(private=path.read_text(), public=path.with_name("host_key.pub")
                          .read_text().strip())
        except (OSError, subprocess.SubprocessError):
            raise HostKeyError("HOST_KEY_UNAVAILABLE") from None
    if not PUBLIC_KEY.fullmatch(key.public):
        raise HostKeyError("HOST_KEY_UNAVAILABLE")
    return key


def user_data(host_key):
    """Install the pinned key as the Droplet's only host key, then restart sshd; nothing else."""
    config = {
        "ssh_deletekeys": True,
        "ssh_genkeytypes": [],
        "ssh_keys": {"ed25519_private": host_key.private, "ed25519_public": host_key.public},
        "runcmd": [RESTART_SSH],
    }
    # JSON is YAML, and it carries the key text exactly.
    return "#cloud-config\n" + json.dumps(config, indent=2) + "\n"


def known_hosts_path(ledger_root, run_id):
    return Path(ledger_root) / "known_hosts" / run_id


def pin_host_key(path, host_key):
    """The run's known_hosts, owner-only: the pinned key, for whatever address the run gets."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(f"* {host_key.public}\n")
