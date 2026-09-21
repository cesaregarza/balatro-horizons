import json
from pathlib import Path
from typing import get_args, get_type_hints

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.config import Config
from balatro_horizons.contracts import Action


def test_dashboard_surfaces_remain_available_when_workbench_is_off(store, episode):
    config = Config()
    app = create_app(store.root, config)
    with TestClient(app) as client:
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
        ):
            response = getattr(client, method)(path, headers=headers, json={}) if method == "post" else getattr(client, method)(path, headers=headers)
            assert response.status_code == 404, (path, response.text)


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
