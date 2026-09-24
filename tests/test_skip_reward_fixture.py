"""A skip reward is selected from the resulting observation, never pre-skip."""

from types import SimpleNamespace as NS

import pytest

from balatro_horizons.evidence.collect import action_table


class OpeningAudit:
    def __init__(self, opens_pack):
        self.opens_pack = opens_pack
        self.actions = []
        self.obs = NS(
            available_action_types=["skip_blind", "select_blind"],
            state=NS(
                revealed_blinds=[NS(id="small")], progress=NS(blind="Small"),
                hand=[NS(id="a"), NS(id="b")], resources=NS(discards=3),
            ),
        )

    def take(self, action):
        kind = action["type"]
        assert kind in self.obs.available_action_types, "ACTION_NOT_AVAILABLE"
        self.actions.append(kind)
        if kind == "skip_blind":
            self.obs.state.progress.blind = "Big"
            self.obs.state.revealed_blinds = [NS(id="big")]
            self.obs.available_action_types = ["skip_pack"] if self.opens_pack else ["select_blind"]
        elif kind == "skip_pack":
            self.obs.available_action_types = ["select_blind"]
        elif kind == "select_blind":
            assert action["blind_id"] == "big"
            self.obs.available_action_types = ["reorder", "discard"]
        elif kind == "reorder":
            self.obs.state.hand = [NS(id=value) for value in action["ordered_ids"]]
        elif kind == "discard":
            self.obs.state.resources.discards -= 1


@pytest.mark.parametrize("opens_pack", [False, True])
def test_skip_reward_is_handled_before_selecting_next_blind(monkeypatch, opens_pack):
    # Keep the real opening actions/assertions and stop before unrelated shop setup.
    monkeypatch.setattr(action_table, "_shop_sequence", lambda: action_table._rows(action_table._shop_rows()))
    audit = OpeningAudit(opens_pack)
    action_table.run_shop_table(audit)
    expected = ["skip_blind"] + (["skip_pack"] if opens_pack else [])
    assert audit.actions == expected + ["select_blind", "reorder", "discard"]


def test_opening_skip_keeps_advance_assertion():
    audit = OpeningAudit(False)
    audit.take = lambda action: None
    with pytest.raises(AssertionError, match="SKIP_BLIND_DID_NOT_ADVANCE"):
        action_table._skip_opening_blind(audit)
