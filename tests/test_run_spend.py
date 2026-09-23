"""Read-only dashboard costs include every attempt, not just committed actions."""

import json

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.review.operator_status import OperatorStatus
from balatro_horizons.review.service import ReviewService
from balatro_horizons.review.spend import run_spend


def event(kind, request_id, **payload):
    return {"type": kind, "request_id": request_id, "payload": payload}


def test_spend_reconciles_reservation_pairs_retries_zero_and_helper_only_responses():
    events = [
        event("provider_reservation", "failed", reserved_usd=0.2),
        event("provider_request", "failed", reserved_usd=0.2),
        event("provider_error", "failed", usage="unknown"),
        event("provider_request", "helper", reserved_usd=0.2),
        event("provider_response", "helper", cost_usd=0.03),
        event("provider_request", "free", reserved_usd=0.2),
        event("provider_response", "free", cost_usd=0),
        event("provider_reservation", "not_sent_yet", reserved_usd=0.2),
    ]
    expected = {"accounted_usd": 0.43, "response_usd": 0.03, "reserved_usd": 0.4}
    assert run_spend(events, None) == pytest.approx(expected)
    assert run_spend(events, {"cost_usd": 0.43}) == pytest.approx(expected)
    # A terminal does not magically convert unknown usage into a known charge.
    assert run_spend(events, {"outcome": "PROVIDER_FAILURE", "cost_usd": 0.43}) == expected


def test_response_cost_may_itself_be_a_conservative_usage_fallback():
    events = [event("provider_request", "one", reserved_usd=0.2),
              event("provider_response", "one", cost_usd=0.2, body={})]
    assert run_spend(events, None) == {
        "accounted_usd": 0.2, "response_usd": 0.2, "reserved_usd": 0,
    }


@pytest.mark.parametrize("cost", [None, -1, True, "0.2", float("nan"), float("inf")])
def test_missing_or_invalid_journal_cost_is_not_displayed_as_zero(cost):
    events = [event("provider_request", "old", reserved_usd=cost)]
    assert run_spend(events, None) == {
        "accounted_usd": None, "response_usd": 0, "reserved_usd": None,
    }
    assert run_spend(events, {"cost_usd": 1.2}) == {
        "accounted_usd": 1.2, "response_usd": None, "reserved_usd": None,
    }


def test_terminal_cost_is_authoritative_without_fabricating_a_breakdown():
    assert run_spend([], {"cost_usd": 2.5}) == {
        "accounted_usd": 2.5, "response_usd": None, "reserved_usd": None,
    }
    assert run_spend([], None) == {"accounted_usd": 0, "response_usd": 0, "reserved_usd": 0}


def test_explorer_and_operator_refresh_the_same_private_safe_episode_costs(store, config):
    eid = store.create({"agent": "fixture", "evidence_kind": "SYNTHETIC_TEST"})
    other = store.create({"agent": "other"})
    store.append(other, "provider_response", {"cost_usd": 99}, request_id="other")
    review = ReviewService(store)
    status = OperatorStatus(store, review)
    with TestClient(create_app(store.root, config)) as client:
        op = client.get("/api/bootstrap").json()["operator_token"]
        opened = client.post("/api/explore/sessions",
                             json={"episode_id": eid, "retrospective": True},
                             headers={"X-BH-Operator": op})
        assert opened.status_code == 200, opened.text
        headers = {"X-Review-Token": opened.json()["review_token"]}
        # No observations or actions yet; costs must still be available.
        first = client.get("/api/explore/decisions", headers=headers).json()
        assert first["spend"]["accounted_usd"] == 0
        store.append(eid, "provider_request", {"reserved_usd": 0.2,
                     "body": {"input": "PRIVATE_PROVIDER_SENTINEL"}}, request_id="one")
        pending = client.get("/api/explore/decisions", headers=headers).json()
        assert pending["spend"] == {
            "accounted_usd": 0.2, "response_usd": 0, "reserved_usd": 0.2,
        }
        store.append(eid, "provider_response", {"cost_usd": 0.003,
                     "body": {"output": "PRIVATE_PROVIDER_SENTINEL"}}, request_id="one")
        path = store.episode_path(eid) / "events.jsonl"
        original = path.read_bytes()
        response = client.get("/api/explore/decisions", headers=headers)
        assert response.status_code == 200
        paid = response.json()
        operator = next(row for row in status.episodes() if row["episode_id"] == eid)
        assert paid["spend"] == operator["spend"] == {
            "accounted_usd": 0.003, "response_usd": 0.003, "reserved_usd": 0,
        }
        assert paid["source_journal_head"] != pending["source_journal_head"]
        assert "PRIVATE_PROVIDER_SENTINEL" not in json.dumps([paid, operator])
        assert path.read_bytes() == original
        assert client.get("/api/explore/decisions").status_code == 403
        store.finish(eid, {"outcome": "PROVIDER_FAILURE", "committed_actions": 0,
                           "cost_usd": 0.003})
        final = client.get("/api/explore/decisions", headers=headers).json()
        operator = next(row for row in status.episodes() if row["episode_id"] == eid)
        assert final["spend"] == operator["spend"] == paid["spend"]
