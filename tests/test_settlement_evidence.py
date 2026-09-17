from copy import deepcopy

import pytest
from test_boundary import project
from test_public_information import native_state

from balatro_horizons.config import ROOT
from balatro_horizons.engine.native_state import normalize
from balatro_horizons.storage.journal import Store


def collection(tmp_path, *, bad_delta=False, bad_public=False, omit_zero_interest=False):
    store = Store(tmp_path)
    eid = store.create({"evidence_kind": "NATIVE", "agent": "evaluator_fixture"}, {})
    for index, money in enumerate(("4", "10")):
        if omit_zero_interest and index == 0:
            continue
        decision = index * 2
        rows = [{"kind": "hands", "label": "Remaining Hands ($1 each)", "dollars": "3"}]
        if index:
            rows.append({"kind": "interest", "label": "$1 per $5, max $5", "dollars": "2"})
        total = str(3 + index * 2)
        settlement = {
            "source": "native_cashout_rows",
            "rows": rows,
            "total": total,
            "omitted_rows": 0,
        }
        before = project(normalize(native_state("ROUND_EVAL"))).model_dump(mode="json")
        before.update(observation_id=decision)
        before["state"]["resources"]["money"] = money
        before["state"]["settlement"] = settlement
        store.private_json(
            eid, f"raw-{decision}.json", {"raw_engine": {"bh": {"settlement": settlement}}}
        )
        if bad_public:
            before["state"]["settlement"]["rows"][0]["label"] = "Wrong visible row"
        store.append(eid, "observation", before, observation_id=decision)
        store.append(
            eid, "action_commit", {"observation_id": decision, "action": {"type": "cash_out"}}
        )
        after = deepcopy(before)
        after.update(observation_id=decision + 1, phase="SHOP")
        after["state"]["settlement"] = None
        after["state"]["resources"]["money"] = str(int(money) + int(total) + int(bad_delta))
        store.append(eid, "observation", after, observation_id=decision + 1)
    return store, [eid]


def test_saved_native_settlement_gate_accepts_matched_collection(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from verify_settlement_evidence import verify

    result = verify(*collection(tmp_path))
    assert result == dict(cashouts=2, interest_rows=1, omitted_interest_rows=1, other_phases=2)


def test_saved_native_settlement_gate_requires_zero_interest_coverage(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from verify_settlement_evidence import verify

    with pytest.raises(AssertionError, match="MISSING_ZERO_INTEREST_CASHOUT"):
        verify(*collection(tmp_path, omit_zero_interest=True))


@pytest.mark.parametrize("defect", ["bad_delta", "bad_public"])
def test_saved_native_settlement_gate_rejects_mismatch(tmp_path, monkeypatch, defect):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from verify_settlement_evidence import verify

    with pytest.raises(AssertionError):
        verify(*collection(tmp_path, **{defect: True}))
