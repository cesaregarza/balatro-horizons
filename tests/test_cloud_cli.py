"""bh cloud: the provider opt-in flag, the controller lock and an offline launch."""

import hashlib
import json

import pytest
from cloud_support import (
    BUNDLE_BYTES,
    BUNDLE_NAME,
    FINGERPRINT,
    FIREWALL_ID,
    RESULTS,
    Cloud,
    FakeTransport,
    admit_preflight,
    cloud_settings,
    worker_status,
)

from balatro_horizons.cli import cloud as cloud_cli
from balatro_horizons.cli import main
from balatro_horizons.cloud.fake import FakeProvider, SimulatedCrash
from balatro_horizons.cloud.ledger import Ledger
from balatro_horizons.cloud.settings import load_settings


@pytest.fixture
def paths(tmp_path, monkeypatch):
    config = tmp_path / "cloud.yaml"
    # Real time: the deadline (3.6 s) bounds the launch test if a trigger is broken.
    settings = cloud_settings(
        heartbeat_grace_seconds=1, poll_seconds=0.05, max_run_hours=0.001,
        cleanup_verify_seconds=2, retry_base_seconds=0.1, retry_max_seconds=0.5,
    )
    config.write_text(json.dumps(settings.model_dump()))
    provider = FakeProvider()
    admit_preflight(provider, settings)
    monkeypatch.setattr(cloud_cli, "client_from_environment", lambda: provider)
    flags = ["--data-dir", str(tmp_path), "--cloud-config", str(config)]
    return flags, Ledger(tmp_path / "cloud"), provider


@pytest.fixture
def access_flags(tmp_path, monkeypatch):
    """An access file for launch, with SSH and ssh-keygen scripted: nothing connects."""
    operator = tmp_path / "operator"
    operator.mkdir()
    (operator / "access_key").write_text("stand-in for the access key; never used\n")
    (operator / BUNDLE_NAME).write_bytes(BUNDLE_BYTES)
    access = tmp_path / "cloud-access.yaml"
    access.write_text(json.dumps({
        "ssh_key_path": str(operator / "access_key"), "ssh_key_fingerprint": FINGERPRINT,
        "firewall_id": FIREWALL_ID,
        "bundle": {"path": str(operator / BUNDLE_NAME), "name": BUNDLE_NAME,
                   "bytes": len(BUNDLE_BYTES), "sha256": hashlib.sha256(BUNDLE_BYTES).hexdigest()},
    }))
    transport = FakeTransport()
    # The worker reports play, then a finished game.
    transport.statuses = [worker_status(), worker_status("finished", "play", actions=5)]
    monkeypatch.setattr(cloud_cli, "OpenSsh", lambda: transport, raising=False)
    return ["--access-config", str(access)]


def run_cli(arguments):
    """The command's exit status; arguments the parser refuses exit with its status."""
    try:
        return main(["cloud", *arguments])
    except SystemExit as stop:
        return stop.code


@pytest.mark.parametrize("command", [["launch", "r1"], ["reconcile"]])
def test_provider_commands_require_the_explicit_flag(paths, monkeypatch, capsys, command):
    flags, ledger, _ = paths

    def forbidden():
        raise AssertionError("no client may be built without the flag")

    monkeypatch.setattr(cloud_cli, "client_from_environment", forbidden)
    assert main(["cloud", *command, *flags]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "PROVIDER_CALLS_NOT_ALLOWED"}
    assert ledger.runs() == []


def test_missing_token_is_reported_without_a_launch(paths, monkeypatch, capsys):
    flags, ledger, _ = paths
    monkeypatch.undo()
    monkeypatch.delenv("DIGITALOCEAN_TOKEN", raising=False)
    assert main(["cloud", "launch", "r1", "--allow-provider-calls", *flags]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "DIGITALOCEAN_TOKEN_MISSING"}
    assert ledger.runs() == []


def test_launch_supervises_until_the_finished_game_is_copied_and_torn_down(paths, access_flags,
                                                                           capsys):
    flags, ledger, provider = paths
    assert run_cli(["launch", "r1", "--allow-provider-calls", *access_flags, *flags]) == 0
    first, _, rest = capsys.readouterr().out.partition("\n")
    assert json.loads(first)["outcome"] == "launched"
    report = json.loads(rest)
    assert report["launches"] == "ALLOWED"
    record = ledger.get("r1")
    assert (record.state, record.teardown_reason) == ("torn_down", "succeeded")
    assert provider.inventory == {}
    assert [run.get("results_copy") for run in report["runs"]] == ["copied"]
    copied = ledger.root / "results" / "r1.json"
    assert copied.is_file() and json.loads(copied.read_text()) == RESULTS


@pytest.mark.parametrize("contents,code", [(None, "CLOUD_ACCESS_MISSING"),
                                           ("bundle: [unclosed", "CLOUD_ACCESS_INVALID")])
def test_only_launch_needs_the_access_settings(paths, tmp_path, monkeypatch, capsys, contents,
                                               code):
    flags, ledger, provider = paths
    access = tmp_path / "cloud-access.yaml"
    if contents is not None:
        access.write_text(contents)
    launch = ["launch", "r1", "--allow-provider-calls", "--access-config", str(access)]
    assert run_cli([*launch, *flags]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": code}
    assert (ledger.runs(), provider.calls) == ([], [])

    def unread(path=None):
        raise AssertionError("only launch reads the access settings")

    # Every teardown path works without them.
    monkeypatch.setattr(cloud_cli, "load_access", unread)
    assert run_cli(["reconcile", "--allow-provider-calls", *flags]) == 0


def test_second_controller_is_refused_while_one_is_alive(paths, access_flags, capsys):
    flags, ledger, provider = paths
    with ledger.controller_lock():
        assert run_cli(["launch", "r1", "--allow-provider-calls", *access_flags, *flags]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "CONTROLLER_BUSY"}
    assert provider.calls == []


def test_cancel_without_the_flag_only_records_the_request(paths, capsys):
    flags, ledger, provider = paths
    provider.boot = False
    Cloud(ledger.root, settings=cloud_settings(), provider=provider).controller().launch("r1")
    # The commands below run on real time, which the fake provider's DELETE check, keyed
    # to the fake clock, cannot match.
    provider.ledger_root = None
    calls = list(provider.calls)
    assert main(["cloud", "cancel", "r1", *flags]) == 0
    assert ledger.get("r1").cancel_requested and provider.calls == calls
    assert main(["cloud", "cancel", "r1", "--allow-provider-calls", *flags]) == 0
    assert ledger.get("r1").state == "torn_down"


def test_status_needs_no_provider_and_reports_an_empty_ledger(paths, capsys):
    flags, _, provider = paths
    assert main(["cloud", "status", *flags]) == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["launches"], report["alerts"], report["runs"]) == ("ALLOWED", [], [])
    assert provider.calls == []


def test_shipped_settings_default_to_one_2_vcpu_8_gib_ubuntu_worker():
    settings = load_settings()
    assert (settings.size, settings.image) == ("s-2vcpu-8gb-amd", "ubuntu-24-04-x64")
    assert settings.max_concurrent_workers == 1
    assert settings.owner_tag == "bh-owner:horizons-main"
    assert settings.worst_case_usd <= settings.infrastructure_budget_usd


def test_shipped_settings_give_the_boot_and_install_twenty_minutes():
    assert load_settings().boot_timeout_seconds == 1200


def test_launch_exits_nonzero_when_cleanup_is_left_unverified(paths, access_flags, capsys):
    flags, ledger, provider = paths
    provider.delete_failures = 10**9
    assert run_cli(["launch", "r1", "--allow-provider-calls", *access_flags, *flags]) == 1
    assert ledger.get("r1").state == "cleanup_unverified"


def unseen_create(ledger, provider):
    """The create was accepted, then the controller died before recording the ID."""
    provider.listing_lag = 10**9
    provider.pending_creates = True
    provider.crash_at.add("after_create")
    with pytest.raises(SimulatedCrash):
        Cloud(ledger.root, settings=cloud_settings(), provider=provider).controller().launch("r1")


def assert_held(ledger, provider, flags, capsys):
    capsys.readouterr()
    assert ledger.get("r1").state == "create_unresolved"
    # Status exits nonzero while an alert is active.
    assert main(["cloud", "status", *flags]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["launches"] == "REFUSED"
    assert "CREATE_UNRESOLVED" in [alert["code"] for alert in report["alerts"]]
    assert [call for call in provider.calls if call[0] == "create"] == [
        ("create", "bh-r1", ("bh-owner:test-ctl", "bh-run:r1"))]
    assert provider.deleted() == []


def test_cancel_request_then_reconcile_holds_an_unseen_create(paths, capsys):
    flags, ledger, provider = paths
    unseen_create(ledger, provider)
    assert main(["cloud", "cancel", "r1", *flags]) == 0
    assert main(["cloud", "reconcile", "--allow-provider-calls", *flags]) == 1
    assert_held(ledger, provider, flags, capsys)


def test_cancel_with_provider_calls_holds_an_unseen_create(paths, capsys):
    flags, ledger, provider = paths
    unseen_create(ledger, provider)
    assert main(["cloud", "cancel", "r1", "--allow-provider-calls", *flags]) == 0
    assert_held(ledger, provider, flags, capsys)
