"""Ledger writes survive power loss: file, rename and directory entries are all synced."""

import os

import pytest
from cloud_support import Cloud


def instrument(monkeypatch, events):
    fsync, replace = os.fsync, os.replace

    def recording_fsync(fd):
        events.append(("fsync", os.readlink(f"/proc/self/fd/{fd}")))
        fsync(fd)

    def recording_replace(source, target):
        events.append(("replace", str(target)))
        replace(source, target)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(os, "replace", recording_replace)


def position(events, kind, path):
    matches = [i for i, event in enumerate(events) if event == (kind, str(path))]
    assert matches, f"no {kind} of {path} in {events}"
    return matches[0]


def test_record_is_synced_renamed_then_its_directory_synced(tmp_path, monkeypatch):
    cloud = Cloud(tmp_path / "cloud")
    cloud.controller().launch("r1")
    events = []
    instrument(monkeypatch, events)
    cloud.controller().cancel("r1")
    runs = cloud.root / "runs"
    target = runs / "r1.json"
    renamed = position(events, "replace", target)
    synced = [i for i, (kind, path) in enumerate(events)
              if kind == "fsync" and path.startswith(str(runs / "")) and path.endswith(".tmp")]
    assert synced and synced[0] < renamed
    assert position(events, "fsync", runs) > renamed


def test_new_directories_are_synced_into_their_parents(tmp_path, monkeypatch):
    events = []
    instrument(monkeypatch, events)
    cloud = Cloud(tmp_path / "data" / "cloud")
    cloud.provider.crash_at.add("before_create")
    with pytest.raises(BaseException, match="before_create"):
        cloud.controller().launch("r1")
    runs = cloud.root / "runs"
    renamed = position(events, "replace", runs / "r1.json")
    for directory in (tmp_path, tmp_path / "data", cloud.root):
        assert position(events, "fsync", directory) < renamed
    assert position(events, "fsync", runs) > renamed


def test_intent_record_is_durable_before_the_create_call(tmp_path, monkeypatch):
    events = []
    instrument(monkeypatch, events)
    cloud = Cloud(tmp_path / "cloud")
    create = cloud.provider.create_droplet

    def recorded_create(spec):
        events.append(("create", spec.name))
        return create(spec)

    monkeypatch.setattr(cloud.provider, "create_droplet", recorded_create)
    cloud.controller().launch("r1")
    created = events.index(("create", "bh-r1"))
    assert position(events, "fsync", cloud.root / "runs") < created
    record = cloud.ledger.get("r1")
    assert record.deadline and record.reserved_usd == pytest.approx(0.2)
