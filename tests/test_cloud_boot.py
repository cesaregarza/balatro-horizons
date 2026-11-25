"""Booting a run: SSH with the pinned host key, the install, and the worker's readiness.

The fake transport stands in for OpenSSH and ssh-keygen, so nothing connects. A run
stays provisioning until the worker reports the game ready, and the boot timeout
covers the whole install.
"""

import json
import shlex
import stat

import pytest
from cloud_support import (
    BUNDLE_BYTES,
    BUNDLE_NAME,
    BUNDLE_SHA256,
    FINGERPRINT,
    PINNED_KEY,
    Cloud,
    worker_status,
)

from balatro_horizons.cloud.ledger import Ledger
from balatro_horizons.cloud.status import status_report

INSTALL = ["probe", "push_worker", "push_worker", "push_bundle", "start"]


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def launched_and_ticked(cloud, ticks, **wiring):
    controller = cloud.controller(**wiring)
    assert controller.launch("r1")["outcome"] == "launched"
    for _ in range(ticks):
        controller.tick()
    return controller


def calls_for(cloud, step):
    return [call for call in cloud.transport.calls if call[0] == step]


def test_an_active_droplet_stays_provisioning_until_the_game_is_ready(cloud):
    cloud.transport.statuses = [b"", worker_status("running", "dependencies"), worker_status()]
    controller = launched_and_ticked(cloud, 0)
    states = []
    for _ in range(4):
        controller.tick()
        states.append(cloud.ledger.get("r1").state)
    assert states == ["provisioning", "provisioning", "provisioning", "running"]


def test_a_ready_game_runs_and_the_run_keeps_the_worker_report(cloud):
    cloud.launched()
    report = getattr(cloud.ledger.get("r1"), "worker_status", None)
    assert report is not None and (report.state, report.stage) == ("ready", "ready")
    assert report.observed["kernel"] == "6.8.0-test"
    assert cloud.transport.steps() == [*INSTALL, "status"]


def test_nothing_is_pushed_until_the_pinned_host_key_answers(cloud):
    cloud.transport.refusals = ["host_key_mismatch", "unreachable", "host_key_mismatch"]
    controller = launched_and_ticked(cloud, 0)
    pin = cloud.root / "known_hosts" / "r1"
    for _ in range(3):
        controller.tick()
        assert cloud.ledger.get("r1").state == "provisioning"
    assert cloud.transport.steps() == ["probe", "probe", "probe"]
    pinned = pin.read_text()
    controller.tick()
    assert cloud.transport.steps()[3:] == INSTALL
    assert pin.read_text() == pinned == f"* {PINNED_KEY}\n"


def test_a_mismatch_that_lasts_to_the_boot_timeout_is_named(cloud):
    cloud.transport.refusals = ["host_key_mismatch"] * 10
    controller = launched_and_ticked(cloud, 0)
    for _ in range(3):
        controller.tick()
        cloud.clock.advance(150)
    controller.tick()
    cloud.assert_torn_down("r1", "host_key_mismatch")


def test_an_unreachable_droplet_at_the_boot_timeout_is_a_boot_failure(cloud):
    cloud.transport.refusals = ["host_key_mismatch"] + ["unreachable"] * 10
    controller = launched_and_ticked(cloud, 0)
    for _ in range(3):
        controller.tick()
        cloud.clock.advance(150)
    controller.tick()
    cloud.assert_torn_down("r1", "boot_failed")


def test_the_reconciler_also_names_a_mismatch_at_the_boot_timeout(cloud):
    cloud.transport.refusals = ["host_key_mismatch"] * 10
    launched_and_ticked(cloud, 1)
    cloud.clock.advance(301)
    cloud.reconcile()
    cloud.assert_torn_down("r1", "host_key_mismatch")


def test_a_failed_stage_tears_down_with_the_stage_as_the_reason(cloud):
    failed = worker_status("failed", "dependencies", "umu.deb sha256 mismatch")
    cloud.transport.statuses = [failed]
    launched_and_ticked(cloud, 2)
    record = cloud.assert_torn_down("r1", "worker_failed:dependencies")
    assert record.worker_status.reason == "umu.deb sha256 mismatch"


@pytest.mark.parametrize("outcome", ["failed", "timed_out"])
@pytest.mark.parametrize("step", ["probe", "push_worker", "push_bundle", "start", "status"])
def test_a_failed_ssh_call_ends_the_boot_with_its_step(cloud, outcome, step):
    cloud.transport.failures = {step: outcome}
    launched_and_ticked(cloud, 2)
    cloud.assert_torn_down("r1", f"ssh_{outcome}:{step}")


def test_a_malformed_status_file_ends_the_boot_with_a_named_reason(cloud):
    cloud.transport.statuses = [b'{"stage": "packages", "state": "lost"}']
    launched_and_ticked(cloud, 2)
    cloud.assert_torn_down("r1", "worker_status_malformed")


def test_a_droplet_without_a_public_ipv4_address_fails_at_once(cloud):
    cloud.provider.no_public_ipv4 = True
    launched_and_ticked(cloud, 1)
    cloud.assert_torn_down("r1", "no_public_ipv4")
    assert cloud.transport.calls == []


def test_a_cancel_during_the_install_tears_down(cloud):
    cloud.transport.statuses = [worker_status("running", "dependencies")]
    controller = launched_and_ticked(cloud, 2)
    assert cloud.ledger.get("r1").state == "provisioning"
    controller.cancel("r1")
    controller.tick()
    cloud.assert_torn_down("r1", "cancelled")


def test_the_boot_timeout_covers_the_install(cloud):
    cloud.transport.statuses = [worker_status("running", "packages")]
    controller = launched_and_ticked(cloud, 1)
    cloud.clock.advance(200)
    controller.tick()
    assert cloud.ledger.get("r1").state == "provisioning"
    cloud.clock.advance(101)
    controller.tick()
    cloud.assert_torn_down("r1", "boot_failed")


def test_a_restart_while_the_worker_installs_tears_the_run_down(cloud):
    cloud.transport.statuses = [worker_status("running", "game")]
    launched_and_ticked(cloud, 2)
    assert cloud.ledger.get("r1").state == "provisioning"
    cloud.restarted().recover()
    cloud.assert_torn_down("r1", "restart_while_provisioning")


def test_the_bundle_push_may_take_only_what_is_left_of_the_boot_timeout(cloud):
    cloud.transport.refusals = ["unreachable"]
    controller = launched_and_ticked(cloud, 1)
    cloud.clock.advance(100)
    controller.tick()
    pushes = calls_for(cloud, "push_bundle")
    assert len(pushes) == 1
    _, _, stdin, timeout = pushes[0]
    assert (stdin, timeout) == (cloud.bundle_path, 200)


def test_the_worker_starts_with_the_bundle_descriptor_and_the_run_deadline(cloud):
    cloud.launched()
    starts = calls_for(cloud, "start")
    assert len(starts) == 1
    command = shlex.split(starts[0][1][-1])
    assert command[:4] == ["systemd-run", "--unit=bh-worker", "--collect",
                           "--property=UMask=0077"]
    assert command[command.index("--bundle-name"):] == [
        "--bundle-name", BUNDLE_NAME, "--bundle-bytes", str(len(BUNDLE_BYTES)),
        "--bundle-sha256", BUNDLE_SHA256,
        "--deadline", cloud.ledger.get("r1").deadline.isoformat()]


def test_every_ssh_call_uses_the_pinned_host_key_and_the_access_key(cloud):
    cloud.launched()
    address = getattr(cloud.ledger.get("r1"), "address", None)
    pin = cloud.root / "known_hosts" / "r1"
    assert cloud.transport.calls and address
    for _, argv, _, timeout in cloud.transport.calls:
        options = argv[1:argv.index("--")]
        assert (argv[0], argv[argv.index("--") + 1], timeout > 0) == (
            "ssh", f"root@{address}", True)
        assert options[options.index("-i") + 1] == str(cloud.key_path)
        assert {f'UserKnownHostsFile="{pin}"', "StrictHostKeyChecking=yes",
                "BatchMode=yes"} <= set(options)


def test_the_address_is_kept_for_ssh_but_never_shown_in_status(cloud):
    cloud.launched()
    address = getattr(cloud.ledger.get("r1"), "address", None)
    assert address and address.startswith("192.0.2.")
    report = status_report(Ledger(cloud.root), cloud.settings, cloud.clock())
    assert address not in json.dumps(report)


def test_the_create_carries_the_registered_key_and_user_data_with_only_the_host_key(cloud):
    cloud.controller().launch("r1")
    specs = getattr(cloud.provider, "specs", [])
    assert len(specs) == 1
    assert getattr(specs[0], "ssh_key", None) == FINGERPRINT
    assert specs[0].user_data.startswith("#cloud-config\n")
    assert json.loads(specs[0].user_data.removeprefix("#cloud-config\n")) == {
        "ssh_deletekeys": True,
        "ssh_genkeytypes": [],
        "ssh_keys": {"ed25519_private": "stand-in private half\n", "ed25519_public": PINNED_KEY},
        "runcmd": [["systemctl", "try-restart", "ssh.service"]],
    }


def test_the_host_key_is_pinned_owner_only_and_its_private_half_is_gone(cloud):
    cloud.controller().launch("r1")
    pin = cloud.root / "known_hosts" / "r1"
    assert pin.is_file() and pin.read_text() == f"* {PINNED_KEY}\n"
    assert stat.S_IMODE(pin.stat().st_mode) == 0o600
    assert stat.S_IMODE(pin.parent.stat().st_mode) == 0o700
    assert len(cloud.transport.key_directories) == 1
    assert not cloud.transport.key_directories[0].exists()


def test_a_host_key_that_cannot_be_made_refuses_before_anything_is_recorded(cloud):
    cloud.transport.keygen_failure = True
    assert cloud.controller().launch("r1") == {
        "outcome": "refused", "run_id": "r1", "reasons": ["HOST_KEY_UNAVAILABLE"]}
    assert cloud.ledger.get("r1") is None and cloud.creates() == []


def test_through_the_ssh_channel_a_ready_game_ends_the_run(cloud):
    controller = cloud.launched(controller=cloud.controller(channel=None))
    controller.tick()
    cloud.assert_torn_down("r1", "succeeded")
    assert cloud.transport.steps() == [*INSTALL, "status", "status"]


def test_through_the_ssh_channel_an_unreadable_worker_is_no_news(cloud):
    controller = cloud.launched(controller=cloud.controller(channel=None))
    cloud.transport.refusals = ["unreachable"] * 5
    cloud.clock.advance(100)
    controller.tick()
    assert cloud.transport.steps() == [*INSTALL, "status", "status"]
    assert cloud.ledger.get("r1").state == "running"
    cloud.clock.advance(21)
    controller.tick()
    cloud.assert_torn_down("r1", "heartbeat_lost")
