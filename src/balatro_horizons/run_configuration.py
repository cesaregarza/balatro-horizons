"""Standard game choices shared with the browser; apply to a launch copy only."""

import json
from pathlib import Path

OPTIONS = json.loads((Path(__file__).parent / "run_options.json").read_text())


def validate_choice(value, kind):
    if value is not None and value not in OPTIONS[kind + "s"]:
        raise ValueError("UNKNOWN_" + kind.upper())
    return value


def apply_choices(config, data):
    # Older clients retain their preset contract. Explicit choices never compete
    # with a preset and never change the saved configuration or an existing run.
    if data.preset == "smoke":
        config.environment.stake = "WHITE"
    if data.deck is not None:
        config.environment.deck = data.deck
    if data.stake is not None:
        config.environment.stake = data.stake


def require_native_selection(environment):
    """Refuse an uncertified choice before creating a run or contacting the game."""
    from balatro_horizons.evidence.certification import require_environment_certificate
    from balatro_horizons.evidence.lock import read_lock
    from balatro_horizons.game.contract import NativeFailure
    from balatro_horizons.game.windows_context import load_session

    load_session()
    try:
        require_environment_certificate(read_lock(), environment)
    except NativeFailure as error:
        raise ValueError(error.code) from None
