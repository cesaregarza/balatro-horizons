import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.config import Config
from balatro_horizons.review.read_cache import VerifiedReadCache
from balatro_horizons.review.service import ReviewService


def test_read_cache_reuses_verified_copy_safe_projection(store):
    eid = store.create({"agent": "heuristic", "evidence_kind": "SYNTHETIC_TEST"})
    store.append(eid, "note", {"items": [1]})
    cache = VerifiedReadCache()
    original = store.events
    calls = 0

    def counted(episode_id):
        nonlocal calls
        calls += 1
        return original(episode_id)

    store.events = counted
    first = cache.events(store, eid)
    first[0]["payload"]["items"].append(2)
    second = cache.events(store, eid)
    assert calls == 1
    assert second[0]["payload"]["items"] == [1]


def test_read_cache_invalidates_on_append_replacement_and_corruption(store, tmp_path):
    eid = store.create({"agent": "heuristic", "evidence_kind": "SYNTHETIC_TEST"})
    store.append(eid, "note", {"value": 1})
    path = store.episode_path(eid) / "events.jsonl"
    cache = VerifiedReadCache()
    assert len(cache.events(store, eid)) == 1
    store.append(eid, "note", {"value": 2})
    assert len(cache.events(store, eid)) == 2

    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(path.read_bytes())
    os.replace(replacement, path)
    assert len(cache.events(store, eid)) == 2

    corrupted = bytearray(path.read_bytes())
    hash_start = corrupted.rfind(b'"hash":"') + len(b'"hash":"')
    corrupted[hash_start] = ord("0") if corrupted[hash_start] != ord("0") else ord("1")
    stamp = path.stat()
    # Some Linux filesystems coalesce ctime updates within a clock tick.
    time.sleep(0.02)
    path.write_bytes(corrupted)
    # Same inode/length and restored mtime: ctime must still invalidate the hit.
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert path.stat().st_ctime_ns != stamp.st_ctime_ns
    with pytest.raises(ValueError, match="JOURNAL_INTEGRITY_FAILURE"):
        cache.events(store, eid)


def test_read_cache_is_bounded_and_serializes_concurrent_readers(store):
    cache = VerifiedReadCache(capacity=2)
    ids = []
    for value in range(3):
        eid = store.create({"agent": "heuristic", "evidence_kind": "SYNTHETIC_TEST"})
        store.append(eid, "note", {"value": value})
        ids.append(eid)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: cache.events(store, ids[0]), range(16)))
    assert all(value == values[0] for value in values)
    assert len(cache._entries) == 1
    cache.events(store, ids[1])
    cache.events(store, ids[2])
    assert len(cache._entries) == 2


def test_read_cache_covers_journals_above_eight_megabytes(store):
    eid = store.create({"agent": "heuristic", "evidence_kind": "SYNTHETIC_TEST"})
    store.append(eid, "large_note", {"content": "x" * (9 * 1024 * 1024)})
    path = store.episode_path(eid) / "events.jsonl"
    assert path.stat().st_size > 8 * 1024 * 1024
    cache = VerifiedReadCache()
    original = store.events
    calls = 0

    def counted(episode_id):
        nonlocal calls
        calls += 1
        return original(episode_id)

    store.events = counted
    first = cache.events(store, eid)
    second = cache.events(store, eid)
    assert first == second and calls == 1
    assert cache._cached_bytes <= cache.max_cache_bytes
    assert cache._entries


def test_decision_summary_reuses_verified_read_for_duplicate_internal_reads(store, episode):
    review = ReviewService(store)
    token = review.open_explorer(episode, include_view=False)["review_token"]
    original = store.events
    calls = 0

    def counted(episode_id):
        nonlocal calls
        calls += 1
        return original(episode_id)

    store.events = counted
    finished, results, failures = Event(), [], []

    def summarize():
        try:
            results.append(review.decisions(token))
        except Exception as error:
            failures.append(error)
        finally:
            finished.set()

    Thread(target=summarize, daemon=True).start()
    assert finished.wait(5), "decision summary stalled while reusing the verified journal"
    assert not failures
    assert results[0]["manifest"]["episode_id"] == episode
    assert calls == 0


def test_explorer_can_skip_initial_view_without_changing_default_contract(store, episode, monkeypatch):
    app = create_app(store.root, Config())
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        monkeypatch.setattr(app.state.review, "_build_view", lambda *_args, **_kwargs: pytest.fail("view built"))
        skipped = client.post("/api/explore/sessions", headers=headers, json={
            "episode_id": episode, "retrospective": True, "include_view": False,
        })
        assert skipped.status_code == 200
        assert skipped.json()["view"] is None
        monkeypatch.undo()
        normal = client.post("/api/explore/sessions", headers=headers, json={
            "episode_id": episode, "retrospective": True,
        })
        assert normal.status_code == 200
        assert normal.json()["view"] is not None


def test_ordinary_cache_keeps_sequence_slots_and_never_changes_exact_records(store):
    eid = store.create({"agent": "heuristic", "evidence_kind": "SYNTHETIC_TEST"})
    store.append(eid, "provider_request", {"body": "request-sentinel" * 1000})
    store.append(eid, "provider_response", {"body": {"output": [
        {"type": "reasoning", "encrypted_content": "opaque-sentinel",
         "summary": [{"type": "summary_text", "text": "Keep the interest."}]},
    ]}})
    store.append(eid, "observation", {"state": "visible-board"}, observation_id=0)
    cache = VerifiedReadCache()
    full = cache.events(store, eid)
    ordinary = cache.events(store, eid, compact=True)
    assert [row["sequence"] for row in ordinary] == [0, 1, 2]
    assert ordinary[0]["payload"] == {}
    assert ordinary[1]["payload"]["body"]["output"][0]["summary"][0]["text"] == "Keep the interest."
    assert ordinary[2] == full[2]
    ordinary[2]["payload"]["state"] = "changed by caller"
    assert cache.events(store, eid, compact=True)[2]["payload"]["state"] == "visible-board"
    assert cache.events(store, eid) == full
    assert cache._cached_bytes == sum(len(blob) for pair in cache._entries.values() for blob in pair)
