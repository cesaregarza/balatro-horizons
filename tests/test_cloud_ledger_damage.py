"""A damaged ledger or settings file is a named error through the dashboard, never a crash or an empty report.

Each case is reached through the route, with a client that turns a server crash into the
500 response a browser would see.
"""

import json
import os

import pytest
import test_cloud_dashboard as dashboard
import yaml
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.cli import main
from balatro_horizons.cloud import settings as cloud_settings_module
from balatro_horizons.config import Config

cloud = dashboard.cloud
LEDGER = {"error": "CLOUD_LEDGER_UNREADABLE"}


def served_response(store):
    with TestClient(create_app(store.root, Config()), raise_server_exceptions=False) as client:
        return dashboard.fetch(client)


def assert_error(response, body):
    assert response.status_code == 503, response.text
    assert response.json() == body


def cloud_paths(store):
    """Everything under the cloud directory. The app's own startup writes elsewhere in the data directory."""
    root = store.root / "cloud"
    return sorted([root, *root.rglob("*")]) if root.exists() else []


def make_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a ledger path")


def make_directory(path):
    path.mkdir(parents=True)


WRONG_TYPE = {
    "cloud is a file": lambda root: make_file(root),
    "runs is a file": lambda root: make_file(root / "runs"),
    "unmatched is a file": lambda root: make_file(root / "unmatched"),
    "inventory is a directory": lambda root: make_directory(root / "inventory.json"),
    "a run record is a directory": lambda root: make_directory(root / "runs" / "r1.json"),
}


@pytest.mark.parametrize("damage", WRONG_TYPE)
def test_a_wrong_type_ledger_path_is_unreadable_and_creates_nothing(store, cloud, damage):
    WRONG_TYPE[damage](cloud.root)
    before = cloud_paths(store)
    assert_error(served_response(store), LEDGER)
    assert cloud_paths(store) == before


@pytest.mark.skipif(os.geteuid() == 0, reason="root can list any directory")
def test_a_runs_directory_that_cannot_be_listed_is_unreadable(store, cloud):
    dashboard.torn_down_run(cloud)
    (cloud.root / "runs").chmod(0)
    try:
        response = served_response(store)
    finally:
        (cloud.root / "runs").chmod(0o700)
    assert_error(response, LEDGER)


def rewrite(cloud, record, change):
    saved = json.loads(record.model_dump_json())
    text = change(saved)
    path = cloud.root / "runs" / f"{record.run_id}.json"
    path.write_text(text if text is not None else json.dumps(saved))


def set_fields(**fields):
    return lambda saved: saved.update(fields)


def first_transition(**fields):
    return lambda saved: saved["history"][0].update(fields)


def infinite_reservation(saved):
    saved["reserved_usd"] = "AMOUNT"
    return json.dumps(saved).replace('"AMOUNT"', "1e999")


NAIVE = "2026-11-24T12:00:00"
MALFORMED = {
    "cleanup_unverified with no history": set_fields(state="cleanup_unverified", history=[]),
    "create_unresolved with no history": set_fields(state="create_unresolved", history=[]),
    "last transition names another state": set_fields(state="tearing_down"),
    "naive created_at": set_fields(created_at=NAIVE),
    "naive time in history": first_transition(at=NAIVE),
    "negative reserved_usd": set_fields(reserved_usd=-1),
    "infinite reserved_usd": infinite_reservation,
}


@pytest.mark.parametrize("damage", MALFORMED)
def test_a_record_the_report_cannot_use_is_unreadable(store, cloud, damage):
    cloud.launched("r1")
    rewrite(cloud, cloud.ledger.get("r1"), MALFORMED[damage])
    before = cloud_paths(store)
    assert_error(served_response(store), LEDGER)
    assert cloud_paths(store) == before


def test_a_torn_down_record_without_its_settled_amount_is_unreadable(store, cloud):
    finished = dashboard.torn_down_run(cloud)
    rewrite(cloud, finished, set_fields(settled_usd=None))
    assert_error(served_response(store), LEDGER)


def test_a_negative_settled_amount_is_unreadable(store, cloud):
    finished = dashboard.torn_down_run(cloud)
    rewrite(cloud, finished, set_fields(settled_usd=-0.5))
    assert_error(served_response(store), LEDGER)


@pytest.mark.parametrize("field,value", [
    ("infrastructure_budget_usd", float("inf")),
    ("infrastructure_budget_usd", float("nan")),
    ("hourly_price_usd", float("inf")),
])
def test_non_finite_settings_are_invalid(store, cloud, tmp_path, monkeypatch, field, value):
    path = tmp_path / "non-finite.yaml"
    path.write_text(yaml.safe_dump(cloud.settings.model_dump() | {field: value}))
    monkeypatch.setattr(cloud_settings_module, "DEFAULT_PATH", path)
    assert_error(served_response(store), {"error": "CLOUD_SETTINGS_INVALID"})


def test_status_command_fails_loudly_on_a_damaged_ledger(store, cloud, tmp_path, capsys):
    make_file(cloud.root / "runs")
    capsys.readouterr()
    flags = ["--data-dir", str(store.root), "--cloud-config", str(tmp_path / "cloud.yaml")]
    assert main(["cloud", "status", *flags]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "LEDGER_DAMAGED"}
