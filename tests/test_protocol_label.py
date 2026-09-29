import json

from balatro_horizons.review.operator_library import OperatorLibrary
from balatro_horizons.review.protocol_label import protocol_label
from balatro_horizons.review.service import ReviewService
from balatro_horizons.storage.journal import atomic_json, digest


def test_library_uses_only_a_bounded_genesis_reference_for_old_harness_labels(store, monkeypatch):
    eid = store.create({"agent": "heuristic", "evidence_kind": "SYNTHETIC_TEST"})
    protocol = {"interface": "agent-context-v7", "private": "not-for-the-browser"}
    path = store.episode_path(eid, True) / "agent-protocol.json"
    atomic_json(path, protocol)
    store.append(eid, "episode_start", {"agent_protocol": {"episode_id": eid, "hash": digest(protocol)}})
    store.append(eid, "note", {"large": "x" * 100_000})
    monkeypatch.setattr(store, "events", lambda *_: (_ for _ in ()).throw(AssertionError("full scan")))
    row = OperatorLibrary(store, ReviewService(store)).episodes()[0]
    assert row["recorded_interface"] == "agent-context-v7"
    assert "not-for-the-browser" not in json.dumps(row)
    atomic_json(path, {**protocol, "interface": "agent-context-v8"})
    assert protocol_label(store, eid) is None


def test_unverified_or_oversized_start_metadata_stays_unknown(store):
    eid = store.create({"agent": "heuristic", "evidence_kind": "SYNTHETIC_TEST"})
    store.append(eid, "episode_start", {"agent_protocol": {"episode_id": eid, "hash": "x"}})
    path = store.episode_path(eid) / "events.jsonl"
    assert protocol_label(store, eid) is None
    event = json.loads(path.read_text())
    event["hash"] = "0" * 64
    path.write_text(json.dumps(event) + "\n")
    assert protocol_label(store, eid) is None
    path.write_text("x" * (64 * 1024 + 1) + "\n")
    assert protocol_label(store, eid) is None
