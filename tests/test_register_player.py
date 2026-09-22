from copy import deepcopy

import pytest

from balatro_horizons.cli import register_player as module
from balatro_horizons.config import ROOT, load_config


def test_registration_preserves_existing_settings_and_rejects_replacement():
    config = load_config(ROOT / "configs/luna-smoke.yaml").public()
    before = deepcopy(config)
    model = {
        **config["models"]["luna"],
        "model": "gpt-5.6-terra",
        "input_usd_per_million": 2.5,
        "output_usd_per_million": 12,
        "pricing_date": "2026-09-15",
    }
    payload = module.settings_payload(config, "terra-tools", model)
    assert config == before
    assert payload["models"]["luna"] == before["models"]["luna"]
    assert payload["models"]["terra-tools"]["settings"] == before["models"]["luna"]["settings"]
    assert payload["budgets"] == before["budgets"]
    assert payload["skills"] == before["skills"]
    with pytest.raises(ValueError, match="EXISTING_PLAYER"):
        module.settings_payload(config, "luna", model)
    with pytest.raises(ValueError, match="INVALID_PLAYER"):
        module.settings_payload(config, "", model)
