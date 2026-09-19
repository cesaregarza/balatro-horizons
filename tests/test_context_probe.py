"""Current cache probe refuses a recorded request that contains the private seed."""

import sys

import pytest
from test_cache_probe import load_probe, probe_config, source

from balatro_horizons.agents.providers import DirectProvider
from balatro_horizons.config import ROOT


def test_probe_scans_candidate_before_preflight_or_provider_send(store, tmp_path, monkeypatch):
    config = probe_config()
    eid = source(store, config, leaked_seed=True)
    before = store.events(eid)
    campaign = tmp_path / "probe"
    probe = load_probe(monkeypatch)
    monkeypatch.setattr(probe, "load_config", lambda path: config)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "probe", "--root", str(store.root), "--episode-id", eid,
            "--config", str(ROOT / "configs/luna-smoke.yaml"),
            "--model", "luna", "--campaign", str(campaign), "--allow-paid",
        ],
    )

    def no_send(*args):
        raise AssertionError("private data must be rejected before send")

    monkeypatch.setattr(DirectProvider, "send", no_send)
    with pytest.raises(ValueError, match="EXPORT_PRIVACY_SCAN_FAILED"):
        probe.main()
    assert not campaign.exists() and store.events(eid) == before
