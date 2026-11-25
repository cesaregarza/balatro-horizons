"""The worker: install the game on this Droplet, start it, and report once it is ready.

The controller copies this file and its pins here, then starts it as root with
systemd-run. It uses only the standard library and imports nothing from the controller's
package. Before each stage it writes the stage to its status file, which the controller
reads over SSH. A failure writes `failed` with the stage and a short reason, then the
worker exits. The game's token and instance ID are made here and never leave the Droplet.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import tomllib
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

RUN_USER = "player"
APT_SECONDS = 900
COMMAND_SECONDS = 120
DISPLAY_SECONDS = 15
TAIL_CHARS = 2000
EVIDENCE_FILES = ("display.out", "display.err", "game.out", "game.err")
BUNDLE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
BUILD_ID = re.compile(r"\b\d+\.\d+\.\d{8}\.\d+\b")

# The launch recipe, as the pilot ran it. The variable names are the game's and the mod's own.
GAME_EXECUTABLE = "Balatro.exe"
MOD_DIRECTORY = "Mods"
DISPLAY = ":99"
XVFB = ("Xvfb", DISPLAY, "-screen", "0", "1280x720x24", "-nolisten", "tcp")
LOOPBACK = "127.0.0.1"
API_PORT = 12346
MOD_SETTINGS = {
    "BALATROBOT_ANIMATION_FPS": "60",
    "BALATROBOT_DEBUG": "0",
    "BALATROBOT_FAST": "0",
    "BALATROBOT_GAMESPEED": "16",
    "BALATROBOT_HEADLESS": "0",
    "BALATROBOT_HOST": LOOPBACK,
    "BALATROBOT_PORT": str(API_PORT),
    "BALATROBOT_RENDER_ON_API": "1",
}
RUNTIME_SETTINGS = {
    "BH_CALIBRATION": "0",
    "BH_ISOLATED_RUNTIME": "1",
    "DISPLAY": DISPLAY,
    "GALLIUM_DRIVER": "llvmpipe",
    "GAMEID": "umu-default",
    "LIBGL_ALWAYS_SOFTWARE": "1",
    "LP_NUM_THREADS": "2",
    "SDL_AUDIODRIVER": "dummy",
    "STORE": "none",
    "WINEDLLOVERRIDES": "version=n,b",
}
# The pilot's limits. Its smaller memory limits ran out of memory.
GAME_LIMITS = ("CPUQuota=200%", "MemoryMax=6656M")
STARTUP_SECONDS = 180
INSPECT_SECONDS = 10
GLXINFO_SECONDS = 20
CLOUD_INIT_SECONDS = 30


class StageFailed(Exception):
    """Ends the current stage; the message is the reason the status file reports."""


class RpcProblem(Exception):
    pass


class Layout:
    """Where things live on the Droplet; tests point it at a temporary directory.

    The worker's own files are root-only. Everything the game uses is under the run's home.
    """

    def __init__(self, worker=Path("/var/lib/bh-worker"), home=Path("/srv/player"),
                 display_socket=Path("/tmp/.X11-unix/X99")):
        self.worker = worker
        self.home = home
        self.display_socket = display_socket
        self.status = worker / "status.json"
        self.log = worker / "worker.log"
        self.env_file = worker / "game.env"
        self.downloads = worker / "downloads"
        self.staged_runner = worker / "staging/runner"
        self.staged_bundle = worker / "staging/bundle"
        self.runners = home / "runners"
        self.game = home / "game"
        self.prefix = home / "prefix"
        self.evidence = home / "evidence"


class Machine:
    """This Droplet: commands, downloads, the display socket and the game's local API."""

    def run(self, argv, *, timeout=COMMAND_SECONDS, check=True, env=None):
        log("run " + argv[0])
        try:
            completed = subprocess.run(argv, capture_output=True, timeout=timeout, check=False,
                                       env=None if env is None else os.environ | env)
        except subprocess.TimeoutExpired:
            raise StageFailed(f"{argv[0]} timed out") from None
        except OSError:
            raise StageFailed(f"{argv[0]} could not start") from None
        if completed.returncode != 0:
            log(f"{argv[0]} exit {completed.returncode}: "
                + completed.stderr.decode(errors="replace")[-500:])
        if check and completed.returncode != 0:
            raise StageFailed(f"{argv[0]} exit {completed.returncode}")
        return completed

    def download(self, url, path, limit):
        """Stop as soon as the file outgrows its pinned size."""
        copied = 0
        try:
            with urllib.request.urlopen(url, timeout=60) as response, path.open("wb") as out:
                while chunk := response.read(1 << 20):
                    copied += len(chunk)
                    if copied > limit:
                        raise StageFailed(f"{path.name} larger than pinned")
                    out.write(chunk)
        except OSError:
            raise StageFailed(f"{path.name} download failed") from None

    def unit_active(self, unit):
        return self.run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0

    def display_answers(self, path):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(2)
            try:
                probe.connect(str(path))
            except OSError:
                return False
        return True

    def post(self, url, body, timeout):
        request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        # The game's API is on loopback, so no proxy may stand in between.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return json.loads(response.read(1 << 20))
        except (OSError, ValueError) as error:
            raise RpcProblem(type(error).__name__) from None

    def sleep(self, seconds):
        time.sleep(seconds)


class Worker:
    def __init__(self, machine, layout, clock=lambda: datetime.now(UTC)):
        self.machine = machine
        self.layout = layout
        self.clock = clock
        self.token = secrets.token_hex(32)
        self.instance_id = uuid.uuid4().hex
        self.job = self.pins = None
        self.status = {"stage": "packages", "state": "running", "reason": None,
                       "started_at": {}, "observed": {}}

    def stages(self):
        return (
            ("packages", self.install_packages), ("dependencies", self.install_dependencies),
            ("bundle", self.unpack_bundle), ("user", self.create_user),
            ("display", self.start_display), ("game", self.start_game),
            ("ready", self.wait_until_ready),
        )

    def run(self, job, pins):
        """Every stage in order, then the final report; the exit status is 0 only when ready."""
        self.job, self.pins = job, pins
        for stage, action in self.stages():
            self.begin(stage)
            try:
                action()
            except StageFailed as failure:
                return self.finish("failed", str(failure))
            except Exception as error:
                return self.finish("failed", f"unexpected {type(error).__name__}")
        return self.finish("ready")

    def abort(self, stage, reason):
        self.begin(stage)
        return self.finish("failed", reason)

    def begin(self, stage):
        log("stage " + stage)
        self.status["stage"] = stage
        self.status["started_at"][stage] = self.clock().isoformat()
        self.write_status()

    def finish(self, state, reason=None):
        self.status.update(state=state, reason=reason and reason[:200],
                           finished_at=self.clock().isoformat())
        self.status["observed"].update(self.observe())
        if state == "failed":
            log("failed: " + reason)
            self.status["evidence"] = self.evidence()
        self.write_status()
        return 0 if state == "ready" else 1

    def write_status(self):
        write_private(self.layout.status, json.dumps(self.status, sort_keys=True))

    def install_packages(self):
        # cloud-init may still be finishing, and first-boot timers can hold the dpkg lock.
        self.machine.run(["cloud-init", "status", "--wait"], timeout=APT_SECONDS, check=False)
        # Its final status is the only record of whether the user data's command failed.
        self.status["observed"]["cloud_init"] = self.capture(
            ["cloud-init", "status", "--long"], CLOUD_INIT_SECONDS)
        self.machine.run(["dpkg", "--add-architecture", "i386"])
        self.apt("update")
        self.apt("install", "-y", *self.pins["apt_packages"])

    def apt(self, *arguments):
        self.machine.run(["apt-get", "-o", "DPkg::Lock::Timeout=600", *arguments],
                         timeout=APT_SECONDS, env={"DEBIAN_FRONTEND": "noninteractive"})

    def install_dependencies(self):
        """Every pinned file is downloaded and checked before any is installed or unpacked."""
        self.layout.downloads.mkdir(parents=True, exist_ok=True)
        files = []
        for pin in self.pins["downloads"]:
            path = self.layout.downloads / pin["name"]
            self.machine.download(pin["url"], path, pin["bytes"])
            verify(path, pin["bytes"], pin["sha256"])
            files.append((pin["use"], path))
        self.apt("install", "-y", *(str(path) for use, path in files if use == "package"))
        runner = next(path for use, path in files if use == "runner")
        # The runner's own top directory is dropped, so its path never depends on it.
        self.unpack(runner, self.layout.staged_runner, "--strip-components=1")
        if not (self.layout.staged_runner / "proton").is_file():
            raise StageFailed("runner has no proton script")

    def unpack_bundle(self):
        bundle = self.layout.worker / self.job.bundle_name
        verify(bundle, self.job.bundle_bytes, self.job.bundle_sha256)
        self.unpack(bundle, self.layout.staged_bundle)
        self.staged_game()

    def unpack(self, archive, destination, *options):
        destination.mkdir(parents=True)
        self.machine.run(["tar", "--extract", f"--file={archive}", f"--directory={destination}",
                          "--no-same-owner", *options], timeout=APT_SECONDS)

    def staged_game(self):
        """The bundle holds either the game directory's contents or the directory itself.

        Either way the game directory has the executable and the mod directory.
        """
        for candidate in (self.layout.staged_bundle, self.layout.staged_bundle / "game"):
            if (candidate / GAME_EXECUTABLE).is_file():
                if not (candidate / MOD_DIRECTORY).is_dir():
                    raise StageFailed("bundle has no mod directory")
                return candidate
        raise StageFailed("bundle has no game executable")

    def create_user(self):
        if self.machine.run(["id", "-u", RUN_USER], check=False).returncode != 0:
            self.machine.run(["useradd", "--home-dir", str(self.layout.home), "--create-home",
                              "--shell", "/bin/bash", "--user-group", RUN_USER])
        self.layout.runners.mkdir(parents=True, exist_ok=True)
        os.rename(self.layout.staged_runner, self.layout.runners / runner_name(self.pins))
        os.rename(self.staged_game(), self.layout.game)
        self.layout.evidence.mkdir(mode=0o700)
        for name in EVIDENCE_FILES:
            (self.layout.evidence / name).touch(mode=0o600)
        self.machine.run(["chown", "-R", f"{RUN_USER}:{RUN_USER}", str(self.layout.home)])

    def start_display(self):
        """Xvfb in its own unit, then a bounded check that display :99 answers."""
        self.start_unit("bh-display", XVFB, "display", self.layout.home)
        give_up = self.clock() + timedelta(seconds=DISPLAY_SECONDS)
        while not self.machine.display_answers(self.layout.display_socket):
            if not self.machine.unit_active("bh-display"):
                raise StageFailed("display exited")
            if self.clock() >= give_up:
                raise StageFailed("display did not answer")
            self.machine.sleep(0.5)
        settings = [f"{name}={value}" for name, value in RUNTIME_SETTINGS.items()]
        glxinfo = ["runuser", "-u", RUN_USER, "--", "env", "-i", "PATH=/usr/bin:/bin",
                   *settings, "glxinfo", "-B"]
        self.status["observed"]["renderer"] = self.capture(glxinfo, GLXINFO_SECONDS)

    def start_game(self):
        write_private(self.layout.env_file, environment_file(self.game_environment()))
        command = ("umu-run", str(self.layout.game / GAME_EXECUTABLE))
        self.start_unit("bh-game", command, "game", self.layout.game,
                        (*GAME_LIMITS, f"EnvironmentFile={self.layout.env_file}"))

    def start_unit(self, unit, command, output, directory, properties=()):
        """A transient unit as the run's user that ends by the run's deadline at the latest."""
        evidence = self.layout.evidence
        self.machine.run([
            "systemd-run", f"--unit={unit}", "--collect", f"--property=User={RUN_USER}",
            f"--property=WorkingDirectory={directory}",
            f"--property=RuntimeMaxSec={self.seconds_left()}", "--property=UMask=0077",
            f"--property=StandardOutput=file:{evidence / (output + '.out')}",
            f"--property=StandardError=file:{evidence / (output + '.err')}",
            *(f"--property={item}" for item in properties), *command,
        ])

    def seconds_left(self):
        """Whole seconds left to the run's own deadline; past it, nothing starts."""
        left = int((self.job.deadline - self.clock()).total_seconds())
        if left <= 0:
            raise StageFailed("deadline passed")
        return left

    def game_environment(self):
        """The recipe's variables and nothing else; the unit inherits no other environment."""
        layout = self.layout
        return MOD_SETTINGS | RUNTIME_SETTINGS | {
            "BH_INSTANCE_ID": self.instance_id,
            "BH_RUNTIME": wine_path(layout.game),
            "BH_TOKEN": self.token,
            "HOME": str(layout.home),
            "LOVELY_MOD_DIR": wine_path(layout.game / MOD_DIRECTORY),
            "PROTONPATH": str(layout.runners / runner_name(self.pins)),
            "PROTON_LOG_DIR": str(layout.evidence),
            "WINEPREFIX": str(layout.prefix),
        }

    def capture(self, command, timeout):
        """A diagnostic command's exit status and capped output; its failure is never fatal."""
        try:
            completed = self.machine.run(command, timeout=timeout, check=False)
        except StageFailed as failure:
            return f"unavailable: {failure}"
        output = completed.stdout.decode(errors="replace")
        return f"exit {completed.returncode}: {output}"[:TAIL_CHARS]

    def wait_until_ready(self):
        """An authenticated inspect must answer MENU within the startup cap."""
        give_up = self.clock() + timedelta(seconds=STARTUP_SECONDS)
        problem = "no answer"
        while self.clock() < give_up:
            if not self.machine.unit_active("bh-game"):
                raise StageFailed("game exited")
            try:
                result = self.inspect()
            except RpcProblem as error:
                problem = str(error)
            else:
                if result.get("state") == "MENU":
                    self.status["observed"]["game_version"] = game_version(result)[:200]
                    return
                problem = f"state {result.get('state')}"
            self.machine.sleep(1)
        raise StageFailed(f"not ready: {problem}")

    def inspect(self):
        request_id = uuid.uuid4().hex
        request = {"jsonrpc": "2.0", "id": request_id, "method": "bh_inspect",
                   "params": {"_bh_token": self.token}}
        reply = self.machine.post(f"http://{LOOPBACK}:{API_PORT}/", request, INSPECT_SECONDS)
        return rpc_result(request_id, reply)

    def observe(self):
        """What this run actually got: steamrt4 and the apt packages are not pinned."""
        observed = {"kernel": os.uname().release}
        for name, read in (("packages_sha256", self.package_digest), ("steamrt4", self.steamrt4)):
            try:
                observed[name] = read()
            except (OSError, StageFailed):
                observed[name] = "unavailable"
        return observed

    def package_digest(self):
        """A digest of every installed package and version, sorted."""
        command = ["dpkg-query", "-W", "-f", "${binary:Package}=${Version}\\n"]
        completed = self.machine.run(command)
        listing = completed.stdout.decode(errors="replace").split("\n")
        return hashlib.sha256("\n".join(sorted(filter(None, listing))).encode()).hexdigest()

    def steamrt4(self):
        """The runtime builds umu fetched, read from its version files, whose format is unpinned."""
        umu = self.layout.home / ".local/share/umu"
        found = []
        for path in sorted(umu.glob("VERSIONS.txt")) + sorted(umu.glob("*/VERSIONS.txt")):
            builds = sorted(set(BUILD_ID.findall(path.read_text(errors="replace"))))
            found.append(f"{path.parent.name}: {' '.join(builds) or 'no build id'}")
        return "; ".join(found)[:TAIL_CHARS] or "not found"

    def evidence(self):
        """Capped log tails, with the token and the instance ID taken out."""
        files = {"stage_log": self.layout.log,
                 "display_stderr": self.layout.evidence / "display.err",
                 "game_stderr": self.layout.evidence / "game.err"}
        return {name: self.redact(tail(path)) for name, path in files.items() if path.is_file()}

    def redact(self, text):
        # The tail is cut after redacting, so a secret split at its start cannot survive.
        redacted = text.replace(self.token, "[token]").replace(self.instance_id, "[instance]")
        return redacted[-TAIL_CHARS:]


def verify(path, size, sha256):
    """Size first, then the SHA256 read in chunks."""
    if path.stat().st_size != size:
        raise StageFailed(f"{path.name} size mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        raise StageFailed(f"{path.name} sha256 mismatch")


def rpc_result(request_id, reply):
    """The reply must answer this request, and an error reply is a failure."""
    if not isinstance(reply, dict) or reply.get("id") != request_id:
        raise RpcProblem("reply id mismatch")
    if reply.get("error") is not None:
        raise RpcProblem("error reply")
    if not isinstance(reply.get("result"), dict):
        raise RpcProblem("no result")
    return reply["result"]


def game_version(result):
    found = result.get("bh")
    return str(found.get("game_version")) if isinstance(found, dict) else "unknown"


def runner_name(pins):
    runner = next(pin for pin in pins["downloads"] if pin["use"] == "runner")
    return runner["name"].removesuffix(".tar.gz")


def wine_path(path):
    return "Z:" + str(path).replace("/", "\\")


def environment_file(environment):
    """Single-quoted values, which systemd reads verbatim, so Wine paths keep their backslashes."""
    if any("'" in value or "\n" in value for value in environment.values()):
        raise StageFailed("environment value cannot be quoted")
    return "".join(f"{name}='{value}'\n" for name, value in sorted(environment.items()))


def write_private(path, text):
    """Owner-only, written beside the target and renamed, so a reader never sees half."""
    partial = path.with_name(path.name + ".part")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)
    os.replace(partial, path)


def tail(path):
    with path.open("rb") as stream:
        stream.seek(max(path.stat().st_size - TAIL_CHARS * 2, 0))
        return stream.read().decode(errors="replace")


def log(message):
    print(f"{datetime.now(UTC).isoformat()} {message}", flush=True)


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        """A bad argument is reported in the status file, never by a bare exit."""
        raise StageFailed("bad arguments")


def parse_job(argv):
    parser = Arguments(prog="worker", add_help=False)
    parser.add_argument("--bundle-name", required=True)
    parser.add_argument("--bundle-bytes", type=int, required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--deadline", type=datetime.fromisoformat, required=True)
    job = parser.parse_args(argv)
    if not BUNDLE_NAME.fullmatch(job.bundle_name) or job.deadline.tzinfo is None:
        raise StageFailed("bad arguments")
    return job


def main(argv=None, *, machine=None, layout=None):
    worker = Worker(machine or Machine(), layout or Layout())
    try:
        job = parse_job(sys.argv[1:] if argv is None else argv)
        pins = tomllib.loads(Path(__file__).with_name("pins.toml").read_text())
    except (StageFailed, OSError, ValueError) as error:
        reason = str(error) if isinstance(error, StageFailed) else "pins unreadable"
        return worker.abort("packages", reason)
    return worker.run(job, pins)


if __name__ == "__main__":
    sys.exit(main())
