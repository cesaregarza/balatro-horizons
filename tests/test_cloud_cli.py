"""bh cloud: the provider opt-in flag, the controller lock and an offline launch."""

import json

import pytest
from cloud_support import Cloud, cloud_settings

from balatro_horizons.cli import cloud as cloud_cli
from balatro_horizons.cli import main
from balatro_horizons.cloud.fake import FakeProvider
from balatro_horizons.cloud.ledger import Ledger
from balatro_horizons.cloud.settings import load_settings


@pytest.fixture
def paths(tmp_path, monkeypatch):
    config = tmp_path / "cloud.yaml"
    # Real time: the deadline (3.6 s) bounds the launch test if a trigger is broken.
    config.write_text(json.dumps(cloud_settings(
        heartbeat_grace_seconds=1, poll_seconds=0.05, max_run_hours=0.001,
        cleanup_verify_seconds=2, retry_base_seconds=0.1, retry_max_seconds=0.5,
    ).model_dump()))
    provider = FakeProvider()
    monkeypatch.setattr(cloud_cli, "client_from_environment", lambda: provider)
    flags = ["--data-dir", str(tmp_path), "--cloud-config", str(config)]
    return flags, Ledger(tmp_path / "cloud"), provider


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


def test_launch_supervises_until_the_silent_worker_is_torn_down(paths, capsys):
    flags, ledger, provider = paths
    assert main(["cloud", "launch", "r1", "--allow-provider-calls", *flags]) == 0
    first, _, rest = capsys.readouterr().out.partition("\n")
    assert json.loads(first)["outcome"] == "launched"
    assert json.loads(rest)["launches"] == "ALLOWED"
    record = ledger.get("r1")
    assert (record.state, record.teardown_reason) == ("torn_down", "heartbeat_lost")
    assert provider.inventory == {}


def test_second_controller_is_refused_while_one_is_alive(paths, capsys):
    flags, ledger, provider = paths
    with ledger.controller_lock():
        assert main(["cloud", "launch", "r1", "--allow-provider-calls", *flags]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "CONTROLLER_BUSY"}
    assert provider.calls == []


def test_cancel_without_the_flag_only_records_the_request(paths, capsys):
    flags, ledger, provider = paths
    provider.boot = False
    Cloud(ledger.root, settings=cloud_settings(), provider=provider).controller().launch("r1")
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
