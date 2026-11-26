"""The run's host key: made with ssh-keygen in a temporary directory, pinned, and sent
in user data that installs it and runs one command."""

import json
import stat
import subprocess
import tempfile

import pytest

from balatro_horizons.cloud.hostkey import (
    HostKey,
    HostKeyError,
    generate_host_key,
    known_hosts_path,
    pin_host_key,
    user_data,
)
from balatro_horizons.cloud.ssh import OpenSsh

KEY = HostKey(private="stand-in private half\n", public="ssh-ed25519 AAAAtestpinnedhostkey")


def test_user_data_installs_only_the_host_key_and_runs_one_command():
    assert user_data(KEY) == """#cloud-config
{
  "ssh_deletekeys": true,
  "ssh_genkeytypes": [],
  "ssh_keys": {
    "ed25519_private": "stand-in private half\\n",
    "ed25519_public": "ssh-ed25519 AAAAtestpinnedhostkey"
  },
  "runcmd": [
    [
      "systemctl",
      "try-restart",
      "ssh.service"
    ]
  ]
}
"""


def test_a_real_key_is_made_in_a_directory_that_is_gone_on_return(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    key = generate_host_key(OpenSsh())
    assert list(tmp_path.iterdir()) == []
    assert key.public.startswith("ssh-ed25519 ") and key.public == key.public.strip()
    # The private half derives the pinned public half, so the pair matches.
    private = tmp_path / "check"
    private.write_text(key.private)
    private.chmod(0o600)
    derived = subprocess.run(["ssh-keygen", "-y", "-f", str(private)], capture_output=True,
                             check=True, timeout=30)
    assert derived.stdout.decode().split()[:2] == key.public.split()


class Keygen:
    def __init__(self, code=0, public="ssh-ed25519 AAAAtestpinnedhostkey \n", error=None):
        self.code, self.public, self.error = code, public, error

    def run(self, argv, *, timeout):
        if self.error:
            raise self.error
        path = argv[argv.index("-f") + 1]
        with open(path, "w") as private, open(path + ".pub", "w") as public:
            private.write("stand-in private half\n")
            public.write(self.public)
        return subprocess.CompletedProcess(argv, self.code, b"", b"")


@pytest.mark.parametrize("keygen", [
    Keygen(code=1),
    Keygen(public="ssh-rsa AAAAnotthepinnedtype\n"),
    Keygen(public="ssh-ed25519 AAAA key with spaces\n"),
    Keygen(error=OSError("no ssh-keygen")),
    Keygen(error=subprocess.TimeoutExpired(["ssh-keygen"], 30)),
])
def test_no_usable_key_is_a_named_refusal(keygen):
    with pytest.raises(HostKeyError, match="HOST_KEY_UNAVAILABLE"):
        generate_host_key(keygen)


def test_the_pin_is_owner_only_and_matches_any_address(tmp_path):
    path = known_hosts_path(tmp_path, "r1")
    pin_host_key(path, KEY)
    pin_host_key(path, KEY)
    assert path == tmp_path / "known_hosts" / "r1"
    assert path.read_text() == "* ssh-ed25519 AAAAtestpinnedhostkey\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_the_user_data_round_trips_as_cloud_config():
    body = user_data(KEY).removeprefix("#cloud-config\n")
    assert json.loads(body)["ssh_keys"]["ed25519_private"] == KEY.private
