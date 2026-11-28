"""The worker's fake machine, for the worker tests: no root, apt, network, display or game.

The fake machine records every command, simulates what the commands would leave on disk,
serves the pinned downloads from memory, and answers the game's API in process, as a
test scripts it or as the fake local API plays one short game.
"""

import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from local_api_support import FakeLocalApi

from balatro_horizons.cloud.worker import worker

START = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
RUNNER = b"stand-in runner archive"
PACKAGE = b"stand-in package"
BUNDLE = b"stand-in game bundle"
BUILD = "4.0.20260101.100000"


def pin(name, data, use):
    return {"name": name, "url": f"https://downloads.invalid/{name}", "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "use": use}


PINS = {
    "apt_packages": ["xvfb", "python3"],
    "downloads": [pin("GE-Proton11-14-x86_64.tar.gz", RUNNER, "runner"),
                  pin("python3-umu.deb", PACKAGE, "package"),
                  pin("umu.deb", PACKAGE, "package")],
}


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


class FakeMachine:
    """Records commands and simulates their effects; each command sees the current stage."""

    def __init__(self, layout, clock):
        self.layout = layout
        self.clock = clock
        self.commands = []
        self.stages = []
        self.files = {p["url"]: data for p, data in zip(PINS["downloads"],
                                                        (RUNNER, PACKAGE, PACKAGE), strict=True)}
        self.user_exists = False
        self.display_up = True
        self.units = {}
        self.posts = []
        # A scripted answer to every request, in place of the local API.
        self.reply = None
        self.local_api = None
        # The committed-move count the status file held as each request was sent.
        self.counts_at_post = []
        self.broken = None
        # The journal's records for the game's unit, each a dict or a raw line; the unit's
        # ControlGroup property; and the commands that time out.
        self.journal = []
        self.control_group = "/system.slice/bh-game.service"
        self.unreadable = set()
        self.timeouts = []

    def run(self, argv, *, timeout=worker.COMMAND_SECONDS, check=True, env=None):
        self.commands.append(list(argv))
        self.timeouts.append(timeout)
        self.stages.append((argv[0], json.loads(self.layout.status.read_text())["stage"]))
        if argv[0] == self.broken:
            raise RuntimeError("the machine broke")
        if argv[0] in self.unreadable:
            raise worker.StageFailed(f"{argv[0]} timed out")
        code = 0
        if argv[0] == "id":
            code = 0 if self.user_exists else 1
        elif argv[0] == "tar":
            self.extract(argv)
        elif argv[0] == "systemd-run":
            self.start(argv)
        if check and code:
            raise worker.StageFailed(f"{argv[0]} exit {code}")
        return subprocess.CompletedProcess(argv, code, self.output(argv), b"")

    def output(self, argv):
        if argv[:3] == ["cloud-init", "status", "--long"]:
            return b"status: done\nerrors: []\n"
        if argv[-2:] == ["glxinfo", "-B"]:
            return b"OpenGL renderer string: llvmpipe (LLVM 17.0.6, 256 bits)\n"
        if argv[0] == "dpkg-query":
            return b"xvfb=2:21.1.12\npython3=3.12.3\n"
        if argv[0] == "journalctl":
            lines = [r if isinstance(r, str) else json.dumps(r) for r in self.journal]
            return "".join(line + "\n" for line in lines).encode()
        if argv[:2] == ["systemctl", "show"]:
            return f"{self.control_group}\n".encode()
        return b""

    def extract(self, argv):
        destination = Path(next(a for a in argv if a.startswith("--directory=")).split("=", 1)[1])
        if "--strip-components=1" in argv:
            (destination / "proton").write_text("stand-in")
            return
        (destination / worker.GAME_EXECUTABLE).write_text("stand-in")
        (destination / worker.MOD_DIRECTORY).mkdir()

    def start(self, argv):
        unit = next(a for a in argv if a.startswith("--unit=")).split("=", 1)[1]
        self.units[unit] = True
        if unit != "bh-game":
            return
        # The game fetches its runtime on first launch, and leaves its token in its stderr.
        umu = self.layout.home / ".local/share/umu/steamrt4"
        umu.mkdir(parents=True)
        (umu / "VERSIONS.txt").write_text(f"depot\t{BUILD}\n")
        environment = {name: value.strip("'") for name, value in (
            line.split("=", 1) for line in self.layout.env_file.read_text().split("\n") if line)}
        token = environment["BH_TOKEN"]
        (self.layout.evidence / "game.err").write_text("x" * 5000 + f"\ntoken={token}\n")
        self.local_api = FakeLocalApi(token, environment["BH_INSTANCE_ID"], end_after=5)

    def download(self, url, path, limit):
        if url not in self.files:
            raise worker.StageFailed(f"{path.name} download failed")
        path.write_bytes(self.files[url])

    def unit_active(self, unit):
        if "systemctl" in self.unreadable:
            raise worker.StageFailed("systemctl timed out")
        if unit == "bh-game" and self.local_api is not None and not self.local_api.running():
            return False
        return self.units.get(unit, False)

    def display_answers(self, path):
        return self.display_up

    def post(self, url, body, timeout):
        self.posts.append((url, body, timeout))
        self.counts_at_post.append(json.loads(self.layout.status.read_text()).get("actions"))
        if self.reply is not None:
            return self.reply(body)
        return self.local_api(url, body, timeout)

    def sleep(self, seconds):
        self.clock.now += timedelta(seconds=seconds)

    def started(self, unit):
        return next(c for c in self.commands if c[0] == "systemd-run" and f"--unit={unit}" in c)


def layout_in(directory):
    layout = worker.Layout(worker=directory / "worker", home=directory / "player",
                           display_socket=directory / "X99")
    # The cgroup tree the game's unit is read from.
    layout.cgroup_root = directory / "cgroup"
    layout.worker.mkdir(parents=True)
    (layout.worker / "bundle.tar").write_bytes(BUNDLE)
    return layout


def job(deadline=START + timedelta(hours=2)):
    """The arguments the controller passes, parsed the way the worker parses them."""
    return worker.parse_job([
        "--bundle-name", "bundle.tar", "--bundle-bytes", str(len(BUNDLE)),
        "--bundle-sha256", hashlib.sha256(BUNDLE).hexdigest(), "--deadline", deadline.isoformat(),
    ])


def run(machine, layout, **arguments):
    """Run every stage and return the exit status, the status file and the worker."""
    runner = worker.Worker(machine, layout, machine.clock)
    code = runner.run(job(**arguments), PINS)
    return code, json.loads(layout.status.read_text()), runner
