"""The dashboard's cloud route and `bh cloud status` print one report, read fresh and honestly."""

import json
from datetime import UTC, datetime

import pytest
from cloud_support import Cloud, cloud_settings
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.cli import main
from balatro_horizons.cloud import settings as cloud_settings_module
from balatro_horizons.config import Config

ROUTE = "/api/cloud/status"
RUN_FIELDS = {"run_id", "state", "created_at", "deadline", "teardown_reason",
              "cost_usd", "reserved_usd", "settled_usd"}


@pytest.fixture
def cloud(store, tmp_path, monkeypatch):
    """Records under the dashboard's data directory, with the default settings file replaced."""
    settings = cloud_settings()
    (tmp_path / "cloud.yaml").write_text(json.dumps(settings.model_dump()))
    monkeypatch.setattr(cloud_settings_module, "DEFAULT_PATH", tmp_path / "cloud.yaml")
    return Cloud(store.root / "cloud", settings=settings)


def fetch(client):
    token = client.get("/api/bootstrap").json()["operator_token"]
    return client.get(ROUTE, headers={"X-BH-Operator": token})


def served(client):
    response = fetch(client)
    assert response.status_code == 200, response.text
    return response.json()


def printed(store, tmp_path, capsys):
    capsys.readouterr()
    flags = ["--data-dir", str(store.root), "--cloud-config", str(tmp_path / "cloud.yaml")]
    assert main(["cloud", "status", *flags]) == 0
    return json.loads(capsys.readouterr().out)


def torn_down_run(cloud, run_id="r1"):
    """A finished run, so its cost is the settled amount and no clock changes it."""
    controller = cloud.launched(run_id)
    cloud.clock.advance(1800)
    controller.cancel(run_id, now=True)
    return cloud.assert_torn_down(run_id, "cancelled")


def without_read_time(report):
    return {key: value for key, value in report.items() if key != "read_at"}


def test_route_needs_the_operator_token(store, cloud):
    with TestClient(create_app(store.root, Config())) as client:
        assert client.get(ROUTE).status_code == 403


def test_nothing_recorded_is_said_plainly_and_nothing_is_created(store, cloud):
    assert not cloud.root.exists()
    with TestClient(create_app(store.root, Config())) as client:
        report = served(client)
    assert (report["launches"], report["refusals"], report["alerts"]) == ("ALLOWED", [], [])
    assert (report["runs"], report["inventory"]) == ([], None)
    assert report["budget_usd"]["total"] == cloud.settings.infrastructure_budget_usd
    assert not cloud.root.exists()


def test_reading_existing_records_creates_no_directory_or_lock(store, cloud):
    torn_down_run(cloud)
    before = sorted(cloud.root.rglob("*"))
    with TestClient(create_app(store.root, Config())) as client:
        served(client)
    assert sorted(cloud.root.rglob("*")) == before


def test_each_alert_has_its_message_and_an_absolute_start_time(store, cloud):
    unmatched = cloud.provider.seed(cloud.settings.owner_tag)
    cloud.reconcile()
    with TestClient(create_app(store.root, Config())) as client:
        report = served(client)
    (alert,) = report["alerts"]
    assert (alert["code"], alert["droplet_id"]) == ("UNMATCHED_DROPLET", unmatched.id)
    assert alert["message"]
    assert datetime.fromisoformat(alert["since"]).utcoffset() is not None
    assert report["launches"] == "REFUSED" and "UNMATCHED_DROPLET" in report["refusals"]


def test_the_time_the_server_read_the_records_is_reported(store, cloud):
    before = datetime.now(UTC)
    with TestClient(create_app(store.root, Config())) as client:
        report = served(client)
    after = datetime.now(UTC)
    assert report.get("read_at") is not None
    assert before <= datetime.fromisoformat(report["read_at"]) <= after


def test_dashboard_and_cli_print_the_same_report(store, cloud, tmp_path, capsys):
    torn_down_run(cloud)
    with TestClient(create_app(store.root, Config())) as client:
        from_dashboard = served(client)
    from_cli = printed(store, tmp_path, capsys)
    assert from_dashboard["runs"]
    assert without_read_time(from_dashboard) == without_read_time(from_cli)


def test_run_costs_add_up_to_the_budget_spent_in_both_reports(store, cloud, tmp_path, capsys):
    finished = torn_down_run(cloud)
    cloud.launched("r2")
    with TestClient(create_app(store.root, Config())) as client:
        reports = [served(client), printed(store, tmp_path, capsys)]
    for report in reports:
        runs = report["runs"]
        assert [set(run) for run in runs] == [RUN_FIELDS, RUN_FIELDS]
        done, live = runs
        assert (done["cost_usd"], done["settled_usd"]) == (finished.settled_usd, finished.settled_usd)
        assert done["reserved_usd"] == finished.reserved_usd
        assert live["settled_usd"] is None and live["cost_usd"] >= live["reserved_usd"]
        budget = report["budget_usd"]
        spent = sum(run["cost_usd"] for run in runs)
        assert spent == pytest.approx(budget["total"] - budget["remaining"], abs=1e-5)


@pytest.mark.parametrize("damaged", ["runs/r1.json", "unmatched/7.json", "inventory.json"])
def test_an_unreadable_record_is_a_named_error_never_an_empty_list(store, cloud, damaged):
    torn_down_run(cloud)
    path = cloud.root / damaged
    path.parent.mkdir(exist_ok=True)
    path.write_text("{not json")
    with TestClient(create_app(store.root, Config())) as client:
        response = fetch(client)
    assert (response.status_code, response.json()) == (503, {"error": "CLOUD_LEDGER_UNREADABLE"})


@pytest.mark.parametrize("content", [
    None, "controller_id: [", '{"controller_id": "x"}', '{"unknown_setting": 1}'])
def test_missing_or_bad_settings_are_a_named_error(store, cloud, tmp_path, monkeypatch, content):
    path = tmp_path / "other.yaml"
    if content is not None:
        path.write_text(content)
    monkeypatch.setattr(cloud_settings_module, "DEFAULT_PATH", path)
    with TestClient(create_app(store.root, Config())) as client:
        response = fetch(client)
    assert (response.status_code, response.json()) == (503, {"error": "CLOUD_SETTINGS_INVALID"})


def test_a_record_written_after_startup_appears_on_the_next_read(store, cloud):
    with TestClient(create_app(store.root, Config())) as client:
        assert served(client)["runs"] == []
        torn_down_run(cloud)
        assert [run["run_id"] for run in served(client)["runs"]] == ["r1"]
        cloud.launched("r2")
        assert [run["run_id"] for run in served(client)["runs"]] == ["r1", "r2"]


def test_a_new_app_on_the_same_directory_returns_the_same_report(store, cloud):
    torn_down_run(cloud)
    with TestClient(create_app(store.root, Config())) as first:
        before = served(first)
    with TestClient(create_app(store.root, Config())) as second:
        after = served(second)
    assert before["runs"]
    assert without_read_time(before) == without_read_time(after)
