"""Frozen synthetic provenance is independent of current runtime support."""

import json
from types import SimpleNamespace

import pytest

from balatro_horizons.review.decision_ledger import summary_input
from balatro_horizons.review.operator_library import OperatorLibrary
from balatro_horizons.review.protocol_label import protocol_metadata
from balatro_horizons.review.service import ReviewService
from balatro_horizons.storage.journal import atomic_json, digest
from balatro_horizons.workbench.routes import _comparison_run


def frozen_run(store, policies):
    eid = store.create({"agent": "heuristic", "evidence_kind": "SYNTHETIC_TEST",
                        "config": {"context_policy": "DO_NOT_BACKFILL"}})
    protocol = {"interface": "tools_v8", "private": "PRIVATE_PROTOCOL_SENTINEL", **policies}
    path = store.episode_path(eid, True) / "agent-protocol.json"
    atomic_json(path, protocol)
    store.append(eid, "episode_start", {"agent_protocol": {"episode_id": eid, "hash": digest(protocol)}})
    return eid, path, protocol


@pytest.mark.parametrize("wire", ["openai_responses_v1", "anthropic_messages_v1"])
def test_frozen_policies_reach_library_and_ledger_without_runtime_support(store, wire):
    policies = {"context_policy": "append_only_decision_v1", "provider_wire_policy": wire}
    eid, path, protocol = frozen_run(store, policies)
    before = (store.episode_path(eid) / "events.jsonl").read_bytes()
    expected = {"recorded_interface": "tools_v8", **policies}
    assert protocol_metadata(store, eid) == expected
    for result in (OperatorLibrary(store, ReviewService(store)).episodes()[0],
                   summary_input(store, eid)["manifest"],
                   _comparison_run(SimpleNamespace(store=store, review=ReviewService(store)), eid)["metadata"]):
        assert {key: result[key] for key in expected} == expected
        assert "PRIVATE_PROTOCOL_SENTINEL" not in json.dumps(result)
    assert (store.episode_path(eid) / "events.jsonl").read_bytes() == before
    atomic_json(path, {**protocol, "context_policy": "changed"})
    assert all(value is None for value in protocol_metadata(store, eid).values())
    assert summary_input(store, eid)["manifest"]["context_policy"] is None


def test_old_absent_or_malformed_policies_remain_unknown(store):
    eid, _, _ = frozen_run(store, {})
    assert protocol_metadata(store, eid) == {
        "recorded_interface": "tools_v8", "context_policy": None, "provider_wire_policy": None}
    assert summary_input(store, eid)["manifest"]["context_policy"] is None
    eid, _, _ = frozen_run(store, {"context_policy": ["bad"], "provider_wire_policy": "x" * 65})
    assert protocol_metadata(store, eid)["context_policy"] is None
    assert protocol_metadata(store, eid)["provider_wire_policy"] is None
