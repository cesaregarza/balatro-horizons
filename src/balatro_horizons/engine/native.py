"""Private Windows transport; never exposed as a provider tool."""

import base64
import hashlib
import json
import selectors
import subprocess
import time
import uuid
from pathlib import Path

from balatro_horizons.config import ROOT
from balatro_horizons.engine.native_state import cards, normalize
from balatro_horizons.engine.windows_context import bridge_environment


class NativeFailure(RuntimeError):
    pass


class NativeRejected(ValueError):
    pass


def instrument_hash(root):
    hashes = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "lovely" not in p.relative_to(root).parts and ".git" not in p.parts
    }
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


class WindowsBridge:
    def __init__(self, environment):
        self.env = environment
        self.root = Path(environment.runtime)
        self._rpc_process = None
        self.calibration = False
        self.lock = None
        self.instance_id = None

    def command(self, mode, input=None):
        command = [
            self.env.powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            r"D:\BalatroHorizonsRuntime\bridge.ps1",
            "-Mode",
            mode,
            "-Port",
            str(self.env.port),
        ]
        if mode in ("launch", "stop") and self.instance_id:
            command.extend(["-InstanceId", self.instance_id])
        if mode == "launch" and self.calibration:
            command.append("-Calibration")
        try:
            if mode == "rpc":
                return self._exchange(command, input)
            if mode == "launch":
                # A Windows GUI descendant can keep WSL's captured pipes open after
                # PowerShell returns. Launch detached; verify readiness through RPC.
                for attempt in range(3):
                    try:
                        subprocess.Popen(
                            command,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                            env=bridge_environment(),
                        )
                        break
                    except OSError as error:
                        # As with RPC process creation, no request was submitted
                        # when WSL fails to open the executable with EIO.
                        if error.errno != 5 or attempt == 2:
                            raise
                        time.sleep(0.2 * (attempt + 1))
                return {"launch_requested": True}
            result = subprocess.run(
                command,
                input=input,
                text=True,
                capture_output=True,
                timeout=self.env.timeout_seconds + 15,
                env=bridge_environment(),
            )
        except ValueError as error:
            raise NativeFailure(str(error)) from None
        except OSError as error:
            raise NativeFailure("WINDOWS_BRIDGE_OS_ERROR_" + str(error.errno)) from None
        except subprocess.TimeoutExpired:
            raise NativeFailure("WINDOWS_BRIDGE_TIMEOUT") from None
        if result.returncode:
            raise NativeFailure("WINDOWS_BRIDGE_FAILED")
        try:
            return json.loads(result.stdout.lstrip("\ufeff"))
        except ValueError:
            raise NativeFailure("WINDOWS_BRIDGE_RESPONSE_INVALID") from None

    def _exchange(self, command, line):
        if self._rpc_process is None or self._rpc_process.poll() is not None:
            # WSL occasionally returns EIO while opening the Windows executable.
            # Retrying process creation is safe: no request has been submitted yet.
            for attempt in range(3):
                try:
                    self._rpc_process = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        bufsize=0,
                        env=bridge_environment(),
                    )
                    break
                except OSError as error:
                    if error.errno != 5 or attempt == 2:
                        raise
                    time.sleep(0.2 * (attempt + 1))
        process = self._rpc_process
        try:
            process.stdin.write(line.encode("utf8"))
            process.stdin.flush()
            result = bytearray()
            deadline = time.monotonic() + self.env.timeout_seconds + 5
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while b"\n" not in result:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise NativeFailure("WINDOWS_BRIDGE_TIMEOUT")
                    chunk = __import__("os").read(process.stdout.fileno(), 65536)
                    if not chunk:
                        raise NativeFailure("WINDOWS_BRIDGE_CLOSED")
                    result.extend(chunk)
                    if len(result) > 16_000_000:
                        raise NativeFailure("NATIVE_RESPONSE_TOO_LARGE")
            return json.loads(result.decode("utf8").lstrip("\ufeff"))
        except (OSError, ValueError, NativeFailure):
            self._close_rpc()
            raise NativeFailure("RPC_TRANSPORT_UNKNOWN") from None

    def _close_rpc(self):
        if self._rpc_process is not None:
            process = self._rpc_process
            self._rpc_process = None
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            if process.stdout:
                process.stdout.close()

    def rpc(self, method, params=None, request_id=None):
        request = {
            "jsonrpc": "2.0",
            "id": request_id or uuid.uuid4().hex,
            "method": method,
            "params": params or {},
        }
        response = self.command("rpc", json.dumps(request) + "\n")
        if "bridge_error" in response:
            raise NativeFailure("RPC_TRANSPORT_UNKNOWN")
        if response.get("id") != request["id"]:
            raise NativeFailure("RPC_RESPONSE_ID_MISMATCH")
        if "error" in response:
            name = response["error"].get("data", {}).get("name")
            if name in ("BAD_REQUEST", "NOT_ALLOWED", "INVALID_STATE"):
                raise NativeRejected("NATIVE_ACTION_REJECTED")
            raise NativeFailure("NATIVE_ENDPOINT_FAILURE")
        return response["result"]

    def verify_files(self):
        path = ROOT / "private/environment.lock.json"
        if not path.is_file():
            raise NativeFailure("NATIVE_RUNTIME_NOT_INSTALLED")
        lock = json.loads(path.read_text())
        for name, key in [
            ("Balatro.exe", "game_sha256"),
            ("version.dll", "injector_sha256"),
            ("bridge.ps1", "bridge_sha256"),
        ]:
            file = self.root / name
            if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != lock[key]:
                raise NativeFailure("ENVIRONMENT_FILE_MISMATCH")
        if instrument_hash(self.root / "Mods") != lock["mods_sha256"]:
            raise NativeFailure("MOD_ENVIRONMENT_MISMATCH")
        self.lock = lock
        return lock

    def launch(self):
        self.verify_files()
        self.instance_id = uuid.uuid4().hex
        self.command("launch")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                state = self.rpc("bh_inspect")
                self.verify_identity(state)
                return state
            except (NativeFailure, NativeRejected):
                time.sleep(0.5)
        raise NativeFailure("NATIVE_STARTUP_HANDSHAKE_TIMEOUT")

    def verify_identity(self, state):
        bh = state.get("bh", {})
        if self.instance_id and bh.get("instance_id") != self.instance_id:
            raise NativeFailure("NATIVE_PROCESS_IDENTITY_MISMATCH")
        if self.lock is not None and bh.get("loaded_manifest") != self.lock:
            raise NativeFailure("LOADED_ENVIRONMENT_MISMATCH")
        if (
            bh.get("identity") != "BalatroHorizons"
            or Path(bh.get("save_directory", "").replace("\\", "/")).name != "BalatroHorizons"
        ):
            raise NativeFailure("SAVE_ISOLATION_FAILED")
        if bh.get("calibration") is not self.calibration:
            raise NativeFailure("RUNTIME_CALIBRATION_POLICY_MISMATCH")
        if bh.get("profile_policy") != "fully_unlocked_v1" or bh.get("headless") or bh.get("fast"):
            raise NativeFailure("RUNTIME_POLICY_MISMATCH")

    def stop(self):
        self._close_rpc()
        self.command("stop")


class NativeGame:
    evidence_kind = "NATIVE"

    def __init__(self, environment, seed, *, calibration=False, launch=True, bridge=None):
        self.environment, self.seed = environment, seed
        if bridge is not None and (
            launch or not calibration or not bridge.calibration or not bridge.instance_id
        ):
            raise NativeFailure("INVALID_BORROWED_NATIVE_BRIDGE")
        self.bridge = bridge if bridge is not None else WindowsBridge(environment)
        self._owns_process = bridge is None
        self._closed = False
        self.bridge.calibration = calibration
        self.lock = self.bridge.verify_files()
        certificate = None
        if not calibration:
            from balatro_horizons.engine.certification import require_environment_certificate

            certificate = require_environment_certificate(self.lock, environment)
        try:
            if launch:
                self.bridge.launch()
            else:
                # Verify the owned process before submitting a menu/start reset.
                if not self.bridge.instance_id:
                    raise NativeFailure("NATIVE_SESSION_REQUIRED")
                self.bridge.verify_identity(self.bridge.rpc("bh_inspect"))
            self.bridge.rpc("menu")
            self.bridge.rpc(
                "start", {"deck": environment.deck, "stake": environment.stake, "seed": seed}
            )
            self.raw = self.bridge.rpc("bh_inspect")
            self.bridge.verify_identity(self.raw)
            self.wait_ready()
        except BaseException:
            self.close()
            raise
        if certificate:
            from balatro_horizons.storage.journal import digest

            if digest(self.raw["bh"]["profile"]) != certificate.get("profile_hashes", {}).get(
                environment.deck + "/" + environment.stake
            ):
                self.close()
                raise NativeFailure("FROZEN_PROFILE_MISMATCH")

    def _require_open(self):
        if self._closed:
            raise NativeFailure("NATIVE_GAME_CLOSED")

    def wait_ready(self):
        self._require_open()
        deadline = time.monotonic() + self.environment.timeout_seconds
        while time.monotonic() < deadline:
            self.raw = self.bridge.rpc("bh_inspect")
            self.bridge.verify_identity(self.raw)
            if self.raw["bh"]["ready"] and not self.raw["bh"]["busy"]:
                return
            time.sleep(0.1)
        raise NativeFailure("NATIVE_SETTLING_TIMEOUT")

    def observe_private(self):
        return normalize(self.raw)

    def terminal_status(self):
        return (
            "WIN"
            if self.raw.get("won") is True
            else "GAME_LOSS"
            if self.raw.get("state") == "GAME_OVER"
            else None
        )

    def locate(self, handle, public_area, issuer):
        key = issuer.resolve_current(handle, public_area)
        area, native_id = key.split(":", 1)
        for i, c in enumerate(cards(self.raw, area)):
            if str(c.get("id", i)) == native_id:
                return area, i
        raise NativeRejected("STALE_NATIVE_OBJECT")

    def apply_public_action(self, action, issuer, request_id=None):
        self._require_open()
        request_id = request_id or uuid.uuid4().hex
        kind = action.type
        simple = {
            "select_blind": "select",
            "skip_blind": "skip",
            "cash_out": "cash_out",
            "leave_shop": "next_round",
            "reroll_shop": "reroll",
        }
        method, params = simple.get(kind, "bh_action"), {}
        if kind in ("play_hand", "discard"):
            params = {
                "action": kind,
                "cards": [self.locate(h, "hand", issuer)[1] for h in action.card_ids],
            }
        elif kind == "reorder":
            method = "rearrange"
            params = {
                action.area: [self.locate(h, action.area, issuer)[1] for h in action.ordered_ids]
            }
        elif kind == "skip_pack":
            params = {"action": "skip_pack"}
        elif kind in ("buy", "choose_pack", "use_consumable", "sell"):
            if kind in ("buy", "choose_pack"):
                handle, area = action.offer_id, "offers"
            elif kind == "use_consumable":
                handle, area = action.consumable_id, "consumables"
            else:
                handle = action.owned_id
                area = issuer._current.get(handle, ("",))[0]
            native_area, index = self.locate(handle, area, issuer)
            params = {"action": kind, "area": native_area, "index": index}
            if kind != "sell":
                params["targets"] = [self.locate(h, "hand", issuer)[1] for h in action.target_ids]
            if kind == "buy":
                params["mode"] = action.mode
        elif kind == "reroll_boss":
            params = {"action": kind}
        try:
            self.bridge.rpc(method, params, request_id)
        except NativeFailure:
            status = self.bridge.rpc("bh_request_status", {"request_id": request_id})
            if status.get("status") == "rejected":
                raise NativeRejected("NATIVE_ACTION_REJECTED") from None
            if status.get("status") != "committed":
                raise NativeFailure("ACTION_STATUS_UNKNOWN") from None
        self.wait_ready()

    def checkpoint(self):
        self._require_open()
        cid = uuid.uuid4().hex
        self.bridge.rpc("save", {"path": f"D:/BalatroHorizonsRuntime/checkpoints/{cid}.jkr"})
        data = (self.bridge.root / "checkpoints" / f"{cid}.jkr").read_bytes()
        return {
            "kind": "native",
            "blob": base64.b64encode(data).decode(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "environment": self.lock,
            "seed": self.seed,
            "phase": self.raw["state"],
        }

    def restore(self, snapshot):
        self._require_open()
        if snapshot["environment"] != self.lock:
            raise NativeFailure("CHECKPOINT_ENVIRONMENT_MISMATCH")
        if snapshot.get("restoration") == "seed_prefix":
            from balatro_horizons.engine.replay import restore_seed_prefix

            restore_seed_prefix(self, snapshot)
            return
        data = base64.b64decode(snapshot["blob"], validate=True)
        if hashlib.sha256(data).hexdigest() != snapshot["sha256"]:
            raise NativeFailure("CHECKPOINT_HASH_MISMATCH")
        name = uuid.uuid4().hex + ".jkr"
        (self.bridge.root / "checkpoints" / name).write_bytes(data)
        self.bridge.rpc("load", {"path": "D:/BalatroHorizonsRuntime/checkpoints/" + name})
        self.wait_ready()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._owns_process and self.bridge.instance_id:
            self.bridge.stop()
        else:
            self.bridge._close_rpc()


class _SessionGame(NativeGame):
    def __init__(self, owner, environment, seed, bridge):
        self.owner = owner
        super().__init__(environment, seed, calibration=True, launch=False, bridge=bridge)

    def _require_open(self):
        super()._require_open()
        if self.owner._failed:
            raise NativeFailure("NATIVE_SESSION_UNAVAILABLE")

    def wait_ready(self):
        try:
            return super().wait_ready()
        except NativeFailure:
            self.owner._failed = True
            raise

    def apply_public_action(self, *args, **kwargs):
        try:
            return super().apply_public_action(*args, **kwargs)
        except NativeFailure:
            self.owner._failed = True
            raise

    def close(self):
        try:
            super().close()
        finally:
            if self.owner._active is self:
                self.owner._active = None


class NativeSession:
    """One owned calibration process; menu/start creates each subsequent game.

    Never used for paid runs or as a substitute for fresh-process restoration
    proofs. A failed/ambiguous operation retires the session; it cannot relaunch
    implicitly or transfer ownership to a process it did not launch.
    """

    def __init__(self, environment, *, reason="startup"):
        if reason not in ("startup", "restoration", "crash_recovery"):
            raise ValueError("INVALID_NATIVE_LAUNCH_REASON")
        self.environment = environment.model_copy(deep=True)
        self.reason = reason
        self.bridge = WindowsBridge(self.environment)
        self.bridge.calibration = True
        self._active = None
        self._started = False
        self._closed = False
        self._failed = False
        self._profiles = {}

    def __enter__(self):
        if self._started or self._closed:
            raise NativeFailure("NATIVE_SESSION_NOT_NEW")
        self._started = True
        try:
            self.bridge.launch()
        except BaseException:
            self.close()
            raise
        return self

    def new_game(self, environment, seed):
        if not self._started or self._closed or self._failed:
            raise NativeFailure("NATIVE_SESSION_UNAVAILABLE")
        if self._active is not None:
            raise NativeFailure("NATIVE_GAME_ALREADY_ACTIVE")
        if environment.model_dump(exclude={"deck", "stake"}) != self.environment.model_dump(
            exclude={"deck", "stake"}
        ):
            raise NativeFailure("NATIVE_SESSION_ENVIRONMENT_CHANGED")
        # Each lease gets its own transport, so fault-injection wrappers and
        # closed pipes cannot leak into the next game. The process nonce stays.
        channel = WindowsBridge(environment)
        channel.calibration = True
        channel.instance_id = self.bridge.instance_id
        try:
            game = _SessionGame(self, environment, seed, channel)
            self._active = game
            from balatro_horizons.storage.journal import digest

            key = (environment.deck, environment.stake)
            profile = digest(game.raw["bh"]["profile"])
            if self._profiles.setdefault(key, profile) != profile:
                raise NativeFailure("FROZEN_PROFILE_MISMATCH")
            return game
        except BaseException:
            self._failed = True
            if self._active is not None:
                self._active.close()
            else:
                channel._close_rpc()
            raise

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._active is not None:
                self._active.close()
        finally:
            if self._started and self.bridge.instance_id:
                self.bridge.stop()
            else:
                self.bridge._close_rpc()

    def __exit__(self, *exc):
        self.close()
