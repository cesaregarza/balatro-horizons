import json
from pathlib import Path
from typing import get_args, get_type_hints

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.config import Config
from balatro_horizons.contracts import Action
from balatro_horizons.review.decision_ledger import ACTION_DETAIL_HANDLERS


def test_dashboard_surfaces_remain_available_when_workbench_is_off(store, episode):
    config = Config()
    app = create_app(store.root, config)
    with TestClient(app) as client:
        assert client.get("/api/bootstrap").json()["workbench"] is False
        token = client.get("/api/bootstrap").json()["operator_token"]
        headers = {"X-BH-Operator": token}
        assert client.get("/api/bootstrap").status_code == 200
        assert client.get("/api/episodes", headers=headers).status_code == 200
        assert client.get("/api/panels", headers=headers).status_code == 200
        assert client.get("/api/batches", headers=headers).status_code == 200
        settings = {"models": {}, "budgets": config.budgets.model_dump(), "skills": config.skills}
        assert client.put("/api/settings", headers=headers, json=settings).status_code == 200
        opened = client.post(
            "/api/explore/sessions",
            headers=headers,
            json={"episode_id": episode, "retrospective": True},
        )
        assert opened.status_code == 200
        session = {"X-Review-Token": opened.json()["review_token"]}
        assert client.get("/api/explore/decisions", headers=session).status_code == 200
        assert client.get("/api/explore/decisions/0", headers=session).status_code == 200
        assert client.get("/api/explore/annotations", headers=session).status_code == 200
        saved = client.post(
            "/api/explore/annotations",
            headers=session,
            json={
                "start_decision": 0,
                "end_decision": 0,
                "judgment": "concern",
                "mechanism_summary": "Keep the future horizon visible.",
                "confidence": "medium",
            },
        )
        assert saved.status_code == 200
        for path, method in (
            ("/api/reviews", "post"),
            ("/api/review", "get"),
            ("/api/review/annotations", "get"),
            ("/api/review/decisions", "get"),
            ("/api/verify", "post"),
            ("/api/branches", "post"),
            ("/api/operator/human", "get"),
            ("/api/operator/runtime", "get"),
        ):
            response = getattr(client, method)(path, headers=headers, json={}) if method == "post" else getattr(client, method)(path, headers=headers)
            assert response.status_code == 404, (path, response.text)


def test_dashboard_annotations_use_requested_retrospective_boundary(store, episode):
    with TestClient(create_app(store.root, Config())) as client:
        operator = client.get("/api/bootstrap").json()["operator_token"]
        opened = client.post(
            "/api/explore/sessions",
            headers={"X-BH-Operator": operator},
            json={"episode_id": episode, "retrospective": True},
        ).json()
        headers = {"X-Review-Token": opened["review_token"]}
        saved = client.post(
            "/api/explore/annotations",
            headers=headers,
            json={
                "start_decision": 4,
                "end_decision": 4,
                "judgment": "concern",
                "mechanism_summary": "The selected decision has a later-horizon tradeoff.",
                "confidence": "medium",
            },
        )
        assert saved.status_code == 200
        assert client.get("/api/explore/annotations", headers=headers).json() == []
        listed = client.get("/api/explore/annotations?decision=4", headers=headers)
        assert listed.status_code == 200
        assert [row["end_decision"] for row in listed.json()] == [4]


def test_explorer_tokens_cannot_mutate_workbench_sessions(store, episode, workbench_config):
    with TestClient(create_app(store.root, workbench_config)) as client:
        operator = client.get("/api/bootstrap").json()["operator_token"]
        opened = client.post(
            "/api/explore/sessions",
            headers={"X-BH-Operator": operator},
            json={"episode_id": episode, "retrospective": True},
        ).json()
        headers = {"X-Review-Token": opened["review_token"]}
        assert client.post("/api/review/advance", headers=headers).json() == {
            "error": "UNKNOWN_REVIEW_SESSION"
        }
        assert client.post(
            "/api/review/seek", headers=headers, json={"decision": 1}
        ).json() == {"error": "UNKNOWN_REVIEW_SESSION"}


def test_server_export_projection_omits_private_fields():
    from balatro_horizons.review.export import export_content, export_response

    ledger = {
        "manifest": {
            "episode_id": "e" * 32,
            "created_at": "2026-09-21T00:00:00Z",
            "agent": "heuristic",
            "evidence_kind": "SYNTHETIC_TEST",
            "evaluation_eligible": False,
            "config": {"deck": "RED", "stake": "GOLD", "models": {}},
            "seed": "DO_NOT_EXPORT_THIS_SEED",
        },
        "summary": None,
        "source_journal_head": "journal-head",
        "actions": [{"decision": 0, "type": "buy", "note": "Save cash", "private": "hidden"}],
        "uncommitted_actions": [],
    }
    content = export_content(ledger, "json", exported_at="fixed-time")
    assert "DO_NOT_EXPORT_THIS_SEED" not in content
    assert '"private"' not in content
    assert '"snapshot_status": "in_progress"' in content
    response = export_response(ledger, "jsonl")
    assert response.headers["content-disposition"].endswith("-partial.jsonl\"")


def test_server_export_escapes_script_delimiters_and_preserves_jsonl_rows():
    from balatro_horizons.review.export import export_content

    note = '<script>alert("x")</script> &\u2028\u2029'
    ledger = {
        "manifest": {
            "episode_id": "e" * 32,
            "agent": "heuristic",
            "evidence_kind": "SYNTHETIC_TEST",
            "evaluation_eligible": False,
            "config": {"deck": "RED", "stake": "GOLD", "models": {}},
        },
        "summary": {"outcome": "GAME_LOSS"},
        "source_journal_head": "journal-head",
        "actions": [{"decision": 31, "type": "buy", "note": note}],
        "uncommitted_actions": [
            {"decision": 31, "type": "buy", "note": note, "status": "rejected"},
            {"decision": 32, "type": "buy", "note": note, "status": "no_committed_transition"},
        ],
    }
    before = json.dumps(ledger, sort_keys=True)
    jsonl = export_content(ledger, "jsonl", exported_at="fixed-time")
    assert "<script>" not in jsonl and "&" not in jsonl
    assert "\u2028" not in jsonl and "\u2029" not in jsonl
    for marker in (
        "full board observations and exact action envelopes",
        "provider prompts, outputs, and opaque continuation data",
        "helper results and agent memory",
        "annotations, private seeds, engine state, and credentials",
    ):
        assert marker in jsonl
    lines = [json.loads(line) for line in jsonl.splitlines()]
    assert [line["decision"] for line in lines] == [31, 32]
    assert lines[0]["committed_actions"][0]["note"] == note
    assert lines[0]["uncommitted_requests"][0]["status"] == "rejected"
    assert lines[1]["committed_actions"] == []
    assert lines[1]["uncommitted_requests"][0]["status"] == "no_committed_transition"
    document = json.loads(export_content(ledger, "json", exported_at="fixed-time"))
    for index, line in enumerate(lines):
        decisions = document["decisions"]
        metadata = {key: value for key, value in document.items() if key != "decisions"}
        assert line == {**metadata, **decisions[index]}
    assert json.dumps(ledger, sort_keys=True) == before
    empty = {**ledger, "actions": [], "uncommitted_actions": []}
    assert export_content(empty, "jsonl", exported_at="fixed-time") == ""
    assert json.loads(export_content(empty, "json", exported_at="fixed-time"))["decisions"] == []


def test_dashboard_factory_rejects_remote_bind_by_default(store):
    with pytest.raises(ValueError, match="REMOTE_BIND_REQUIRES_ALLOW_REMOTE"):
        create_app(store.root, Config(), bind_host="0.0.0.0")


def test_action_descriptor_covers_contract_actions():
    descriptor_path = Path(__file__).parents[1] / "web/src/actionDescriptors.json"
    descriptors = json.loads(descriptor_path.read_text())
    action_types = {
        get_type_hints(action).get("type").__args__[0]
        for action in get_args(Action)
    }
    assert action_types <= descriptors.keys()


def test_action_detail_handlers_cover_contract_actions():
    action_types = {
        get_type_hints(action).get("type").__args__[0]
        for action in get_args(Action)
    }
    assert action_types == set(ACTION_DETAIL_HANDLERS)


def test_doctor_failure_uses_process_exit_code_without_payload_key(monkeypatch, capsys):
    from balatro_horizons.cli import commands, main

    monkeypatch.setattr(commands, "load_config", lambda _: Config())
    monkeypatch.setattr(commands, "doctor", lambda *_: {"blockers": ["NATIVE_RUNTIME_NOT_INSTALLED"]})
    assert main(["doctor", "--config", "unused.yaml"]) == 1
    assert "exit_code" not in capsys.readouterr().out


def test_review_parser_defaults_off_and_rejects_remote_hosts():
    from balatro_horizons.cli.parser import build_parser

    parser = build_parser()
    assert parser.parse_args(["review"]).workbench is False
    assert parser.parse_args(["review", "--workbench"]).workbench is True
    with pytest.raises(SystemExit):
        parser.parse_args(["review", "--host", "0.0.0.0"])


def test_review_dispatch_passes_workbench_flag_without_launching_server(monkeypatch):
    from balatro_horizons.cli.commands import dispatch
    from balatro_horizons.cli.parser import build_parser

    created = []
    launched = []

    def fake_create_app(data_dir, **kwargs):
        created.append((data_dir, kwargs))
        return object()

    def fake_uvicorn_run(app, **kwargs):
        launched.append((app, kwargs))

    monkeypatch.setattr("balatro_horizons.api.app.create_app", fake_create_app)
    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)
    parser = build_parser()
    for arguments, expected in ((["review"], False), (["review", "--workbench"], True)):
        assert dispatch(parser.parse_args(arguments)) is None
        assert created[-1][1]["workbench_enabled"] is expected
    assert len(created) == len(launched) == 2
