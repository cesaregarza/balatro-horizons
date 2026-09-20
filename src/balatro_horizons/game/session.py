"""Owned native game leases and calibration sessions."""

import base64
import hashlib
import time
import uuid
from contextlib import contextmanager

from balatro_horizons.game.actions import native_request
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.game.environment import windows_runtime_path
from balatro_horizons.game.state.normalize import normalize
from balatro_horizons.game.transport import WindowsBridge, raise_rpc_error


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
            from balatro_horizons.evidence.certification import require_environment_certificate

            certificate = require_environment_certificate(self.lock, environment)
        try:
            if launch:
                self.bridge.launch()
            else:
                # Verify the owned process before submitting a menu/start reset.
                if not self.bridge.instance_id:
                    raise NativeFailure("NATIVE_SESSION_REQUIRED")
                self.bridge.verify_identity(self.bridge.rpc("bh_inspect"))
            self.start_run(environment.deck, environment.stake, seed)
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

    def start_run(self, deck, stake, seed):
        """Evaluator-only reset/start; never offered to the agent."""
        self.bridge.rpc("menu")
        self.bridge.rpc("start", {"deck": deck, "stake": stake, "seed": seed})

    def fixture(self, case):
        self._require_open()
        return self.bridge.rpc("bh_fixture", {"case": case})

    def rules(self):
        self._require_open()
        return self.bridge.rpc("bh_rules")

    def inspect_raw(self):
        self._require_open()
        return self.raw

    def replay_request(self, method, params, request_id):
        """Calibration-only duplicate-ID probe; never a provider action."""
        self._require_open()
        if not self.bridge.calibration:
            raise NativeFailure("EVALUATOR_ONLY")
        return self.bridge.rpc(method, params, request_id)

    @contextmanager
    def intercept_rpc_for_calibration(self, wrapper):
        """Temporarily inject a transport fault in a calibration process only."""
        self._require_open()
        if not self.bridge.calibration:
            raise NativeFailure("EVALUATOR_ONLY")
        original = self.bridge.rpc
        self.bridge.rpc = wrapper(original)
        try:
            yield
        finally:
            self.bridge.rpc = original

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

    def apply_public_action(self, action, issuer, request_id=None):
        self._require_open()
        request_id = request_id or uuid.uuid4().hex
        method, params = native_request(action, self.raw, issuer)
        try:
            self.bridge.rpc(method, params, request_id)
        except NativeFailure as error:
            # Only an uncertain transport acknowledgement may be reconciled by
            # request ID. A definite BUSY/NOT_READY error is not a legal move.
            if error.name is not None:
                raise
            status = self.bridge.rpc("bh_request_status", {"request_id": request_id})
            if status.get("status") == "rejected":
                raise_rpc_error(status.get("response"))
            if status.get("status") != "committed":
                raise NativeFailure("ACTION_STATUS_UNKNOWN") from None
        self.wait_ready()

    def checkpoint(self):
        self._require_open()
        cid = uuid.uuid4().hex
        self.bridge.rpc("save", {"path": self._checkpoint_rpc_path(cid + ".jkr")})
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
            from balatro_horizons.game.replay import restore_seed_prefix

            restore_seed_prefix(self, snapshot)
            return
        data = base64.b64decode(snapshot["blob"], validate=True)
        if hashlib.sha256(data).hexdigest() != snapshot["sha256"]:
            raise NativeFailure("CHECKPOINT_HASH_MISMATCH")
        name = uuid.uuid4().hex + ".jkr"
        (self.bridge.root / "checkpoints" / name).write_bytes(data)
        self.bridge.rpc("load", {"path": self._checkpoint_rpc_path(name)})
        self.wait_ready()

    def _checkpoint_rpc_path(self, name):
        runtime = windows_runtime_path(self.environment.runtime).replace("\\", "/")
        return runtime.rstrip("/") + "/checkpoints/" + name

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
