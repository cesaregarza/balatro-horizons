"""Status shows exactly what blocks launches, from the durable blocking records alone.

Each test crashes right after the durable write that commits a blocker, then reads
the directory with a fresh ledger and controller. Nothing else is written after
that point, so the alert must come from the blocker itself.
"""

import json

import pytest
from cloud_support import Cloud

from balatro_horizons.cli import main
from balatro_horizons.cloud.fake import SimulatedCrash

NEVER = 10**9


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def crash_after_saving(cloud, monkeypatch, state):
    save = cloud.ledger.save

    def saving(record):
        save(record)
        if record.state == state:
            raise SimulatedCrash(f"after committing {state}")

    monkeypatch.setattr(cloud.ledger, "save", saving)


def assert_blocked(cloud, capsys, code, subject):
    config = cloud.root.parent / "cloud.yaml"
    config.write_text(json.dumps(cloud.settings.model_dump()))
    capsys.readouterr()
    flags = ["--data-dir", str(cloud.root.parent), "--cloud-config", str(config)]
    exit_code = main(["cloud", "status", *flags])
    report = json.loads(capsys.readouterr().out)
    assert [(a["code"], a[subject[0]]) for a in report["alerts"]] == [(code, subject[1])]
    assert report["alerts"][0]["message"]
    assert exit_code == 1
    creates = len(cloud.creates())
    refused = cloud.restarted().launch("r2")
    assert refused["outcome"] == "refused" and code in refused["reasons"]
    assert len(cloud.creates()) == creates


def test_crash_after_committing_an_unresolved_create_still_shows_its_alert(
    cloud, monkeypatch, capsys,
):
    cloud.provider.fail_create = True
    cloud.provider.fail_create_code = "HTTP_503"
    crash_after_saving(cloud, monkeypatch, "create_unresolved")
    with pytest.raises(SimulatedCrash):
        cloud.controller().launch("r1")
    monkeypatch.undo()
    assert_blocked(cloud, capsys, "CREATE_UNRESOLVED", ("run_id", "r1"))


def test_crash_after_committing_an_unverified_cleanup_still_shows_its_alert(
    cloud, monkeypatch, capsys,
):
    controller = cloud.launched()
    cloud.provider.delete_failures = NEVER
    crash_after_saving(cloud, monkeypatch, "cleanup_unverified")
    with pytest.raises(SimulatedCrash):
        controller.cancel("r1", now=True)
    monkeypatch.undo()
    assert_blocked(cloud, capsys, "CLEANUP_UNVERIFIED", ("run_id", "r1"))


def test_crash_after_committing_an_unmatched_droplet_still_shows_its_alert(
    cloud, monkeypatch, capsys,
):
    unmatched = cloud.provider.seed(cloud.settings.owner_tag)
    save_unmatched = cloud.ledger.save_unmatched

    def saving(record):
        save_unmatched(record)
        raise SimulatedCrash("after committing the unmatched watch")

    monkeypatch.setattr(cloud.ledger, "save_unmatched", saving)
    with pytest.raises(SimulatedCrash):
        cloud.reconcile()
    monkeypatch.undo()
    assert_blocked(cloud, capsys, "UNMATCHED_DROPLET", ("droplet_id", unmatched.id))

