import importlib.util

from balatro_horizons.config import ROOT


def test_completed_fixture_receipts_reach_both_provider_contexts(store, episode):
    spec = importlib.util.spec_from_file_location(
        "verify_cost_evidence", ROOT / "scripts/verify_cost_evidence.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.verify(store, episode)
    assert report["provider_contexts_checked"] == report["observations"] * 2
    assert report["persisted_receipts_checked"] > 0
    assert report["reconstructed_receipts_checked"] == 0
    assert "buy" in report["transaction_types"]
