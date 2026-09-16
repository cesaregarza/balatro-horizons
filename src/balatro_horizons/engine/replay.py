"""Certified seed-prefix restoration using recorded public actions, never a solver."""

import uuid

from balatro_horizons.contracts import ActionEnvelope, RemainingBudget
from balatro_horizons.engine.provenance import continuation_fingerprint
from balatro_horizons.observations.projection import HandleIssuer, project_public


class ReplayDivergence(ValueError):
    def __init__(self, code, game, decision=None):
        super().__init__(code)
        self.actual = game.observe_private()
        self.decision = decision


def check_private(game, expected_hash, decision=None):
    if expected_hash and continuation_fingerprint(game.observe_private()) != expected_hash:
        raise ReplayDivergence("PRIVATE_CONTINUATION_DIVERGENCE", game, decision)


def replay_steps(game, issuer, steps, eid):
    for step in steps:
        if step["kind"] == "fixture":
            game.bridge.rpc("bh_fixture", {"case": step["case"]})
        else:
            envelope = ActionEnvelope.model_validate(step["envelope"])
            game.apply_public_action(envelope.action, issuer, uuid.uuid4().hex)
        game.wait_ready()
        expected = step["observation"]
        actual = project_public(
            game.observe_private(),
            episode_id=eid,
            observation_id=expected["observation_id"],
            issuer=issuer,
            memory=expected["memory"],
            remaining_budget=RemainingBudget(game_actions=0, provider_calls=0),
        )
        if actual.public_state_hash != expected["public_state_hash"]:
            raise ReplayDivergence("PUBLIC_REPLAY_DIVERGENCE", game, expected["observation_id"])
        check_private(game, step.get("continuation_hash"), expected["observation_id"])
    return issuer


def restore_seed_prefix(game, snapshot):
    if snapshot.get("environment") is not None and game.lock != snapshot["environment"]:
        raise ValueError("REPLAY_ENVIRONMENT_MISMATCH")
    game.wait_ready()
    check_private(game, snapshot.get("initial_continuation_hash"), 0)
    issuer = HandleIssuer.restore(snapshot["initial_issuer"])
    return replay_steps(game, issuer, snapshot["steps"], snapshot["episode_id"])
