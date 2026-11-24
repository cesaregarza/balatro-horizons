"""bh review --view-only serves the dashboard without the routes that act for the operator."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import app as api_app
from balatro_horizons.api import create_app
from balatro_horizons.cli import main
from balatro_horizons.cli.credentials import ALLOWED as MODEL_KEYS
from balatro_horizons.cloud import digitalocean
from balatro_horizons.cloud.settings import TOKEN_VARIABLE
from balatro_horizons.config import Config
from balatro_horizons.harness.transport.base import Transport
from balatro_horizons.service import RunService

ABSENT = {
    ("/api/runs", "post"), ("/api/stop", "post"), ("/api/settings", "put"),
    ("/api/panels", "post"), ("/api/batches", "post"),
    ("/api/batches/{batch_id}/run", "post"), ("/api/batches/{batch_id}/export", "post"),
    ("/api/explore/annotations", "post"),
}
# What the library and the explorer still write: exposure and session bookkeeping.
BOOKKEEPING = {
    ("/api/operator/status", "post"), ("/api/operator/episodes", "post"),
    ("/api/explore/sessions", "post"), ("/api/explore/seek", "post"),
}
SENTINEL = "sentinel-value-that-must-never-appear"
SECRETS = (TOKEN_VARIABLE, *sorted(MODEL_KEYS))


def routes_of(app):
    return {
        (path, method) for path, methods in app.openapi()["paths"].items()
        for method in methods if method in {"get", "post", "put", "patch", "delete"}
    }


def run_cli(argv):
    try:
        return main(argv)
    except SystemExit as stop:  # argparse rejecting a flag the command does not know
        return stop.code


@pytest.fixture
def served(monkeypatch):
    """The apps the review command would serve, captured instead of listening."""
    apps = []
    monkeypatch.setattr("uvicorn.run", lambda app, **options: apps.append(app))
    return apps


def view_only_app(store, served, *flags):
    assert run_cli(["--data-dir", str(store.root), "review", "--view-only", *flags]) == 0
    assert len(served) == 1
    return served[0]


def operator(client):
    return {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}


def test_view_only_mounts_exactly_the_routes_that_do_not_act(store, served):
    flag_off = routes_of(create_app(store.root, Config()))
    view_only = routes_of(view_only_app(store, served))
    assert view_only == flag_off - ABSENT
    assert ("/api/cloud/status", "get") in view_only
    assert {route for route in view_only if route[1] != "get"} == BOOKKEEPING
    assert len(view_only) == 19


def test_every_excluded_route_answers_404_or_405(store, served):
    with TestClient(view_only_app(store, served)) as client:
        headers = operator(client)
        for path, method in sorted(ABSENT):
            url = path.replace("{batch_id}", "a" * 32)
            response = client.request(method.upper(), url, headers=headers, json={})
            assert response.status_code in (404, 405), (path, method, response.status_code)


def test_bootstrap_reports_the_mode(store, served):
    with TestClient(view_only_app(store, served)) as client:
        assert client.get("/api/bootstrap").json().get("view_only") is True
    with TestClient(create_app(store.root, Config())) as client:
        assert client.get("/api/bootstrap").json().get("view_only") is False


def test_view_only_with_workbench_is_refused_at_startup(store, served, capsys):
    argv = ["--data-dir", str(store.root), "review", "--view-only", "--workbench"]
    assert run_cli(argv) == 1
    assert json.loads(capsys.readouterr().err) == {"error": "VIEW_ONLY_EXCLUDES_WORKBENCH"}
    assert served == []


def test_view_only_keeps_what_the_library_and_explorer_need(store, episode, served):
    with TestClient(view_only_app(store, served)) as client:
        headers = operator(client)
        assert [row["episode_id"] for row in client.get("/api/episodes", headers=headers).json()] == [episode]
        body = {"episode_ids": [episode]}
        assert client.post("/api/operator/episodes", headers=headers, json=body).status_code == 200
        assert client.post("/api/operator/status", headers=headers, json=body).status_code == 200
        opened = client.post("/api/explore/sessions", headers=headers,
                             json={"episode_id": episode, "retrospective": True})
        assert opened.status_code == 200
        session = {"X-Review-Token": opened.json()["review_token"]}
        assert client.get("/api/explore/decisions", headers=session).status_code == 200
        assert client.get("/api/explore/annotations", headers=session).status_code == 200
        assert client.post("/api/explore/seek", headers=session, json={"decision": 0}).status_code == 200


def test_review_serves_the_data_directory_given_before_the_command(store, monkeypatch):
    seen = []
    monkeypatch.setattr(api_app, "create_app", lambda data_dir, **options: seen.append(data_dir))
    monkeypatch.setattr("uvicorn.run", lambda app, **options: None)
    assert run_cli(["--data-dir", str(store.root), "review"]) == 0
    assert seen == [store.root]


def test_no_view_only_route_builds_a_provider_client_or_reads_a_secret(
    store, episode, served, monkeypatch,
):
    for name in SECRETS:
        monkeypatch.setenv(name, SENTINEL)
    built = []

    def refuse(what):
        def constructor(*args, **options):
            built.append(what)
            raise AssertionError(f"{what} must not be reached in view-only mode")
        return constructor

    monkeypatch.setattr(digitalocean, "client_from_environment", refuse("cloud client"))
    monkeypatch.setattr(digitalocean.DigitalOceanClient, "__init__", refuse("cloud client"))
    monkeypatch.setattr(Transport, "__init__", refuse("model client"))
    for method in ("start", "policy", "create_game", "run_batch"):
        monkeypatch.setattr(RunService, method, refuse("game or run " + method))
    app = view_only_app(store, served)
    visited = set()
    with TestClient(app, raise_server_exceptions=False) as client:
        headers = operator(client) | {"X-Review-Token": SENTINEL[::-1]}
        for path, method in sorted(routes_of(app)):
            url = re.sub(r"\{[^}]+\}", "0", path)
            response = client.request(method.upper(), url, headers=headers, json={})
            assert SENTINEL not in response.text, path
            visited.add((path, method))
    assert built == []
    assert visited == routes_of(app)
