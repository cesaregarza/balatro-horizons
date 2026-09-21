"""The declared policy and game seams, with synthetic-only delivery."""

import threading
from types import SimpleNamespace

from test_openai_luna import luna

from balatro_horizons.agents.baselines import Baseline, ScriptedPolicy
from balatro_horizons.agents.providers import DirectProvider
from balatro_horizons.game.contract import GameSession
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.contract import Policy, ProviderPolicy
from balatro_horizons.runner import Runner
from balatro_horizons.service import HumanPolicy, HumanSequencePolicy, InterventionPolicy


def test_concrete_policies_satisfy_the_declared_contract():
    config = luna()
    provider = DirectProvider(config.models["luna"], config.budgets)
    policies = (
        provider,
        HumanPolicy(threading.Event()),
        Baseline("heuristic"),
        ScriptedPolicy([{"kind": "abort", "reason": "synthetic"}]),
    )
    assert all(isinstance(policy, Policy) for policy in policies)
    assert isinstance(provider, ProviderPolicy)
    assert not isinstance(policies[1], ProviderPolicy)
    assert isinstance(FakeGame(), GameSession)
    provider.client.close()


def test_interventions_switch_from_human_or_scripted_to_metered_provider():
    config = luna()
    provider = DirectProvider(config.models["luna"], config.budgets)
    human = HumanPolicy(threading.Event())
    try:
        override = InterventionPolicy([{"type": "skip_blind"}], provider)
        sequence = HumanSequencePolicy(human, override, 1)
        assert isinstance(sequence, Policy)
        assert not isinstance(sequence, ProviderPolicy)
        assert sequence.active_policy is human
        sequence.on_commit()
        assert sequence.active_policy is override
        assert not isinstance(override.active_policy, ProviderPolicy)
        override.decide(SimpleNamespace(observation={"observation_id": 0}), [])
        assert override.active_policy is provider
        assert isinstance(override.active_policy, ProviderPolicy)
    finally:
        provider.client.close()


def test_committed_action_ends_provider_continuation_once(store):
    config = luna()
    config.budgets.paid_calls_enabled = True

    class ProbeProvider(DirectProvider):
        def __init__(self):
            super().__init__(config.models["luna"], config.budgets)
            self.ended = 0

        def request(self, ctx, exchanges):
            if ctx["observation"]["observation_id"] == 1:
                assert self.last_provider_turn is None
                assert self.last_tool_call is None
            return {"context": dict(ctx), "exchanges": exchanges}

        def check_input(self, body):
            return None

        def send(self, body):
            return body

        def usage_cost(self, response, reserved):
            return 0.0

        def parse(self, response):
            self.last_provider_turn = {"provider": "openai", "items": []}
            self.last_tool_call = {"name": "synthetic", "arguments": {}}
            observation = response["context"]["observation"]
            if observation["observation_id"]:
                return {"kind": "abort", "reason": "continuation checked"}
            return {
                "kind": "action",
                "envelope": {
                    "observation_id": 0,
                    "action": {
                        "type": "select_blind",
                        "blind_id": observation["state"]["revealed_blinds"][0]["id"],
                    },
                },
            }

        def on_decision_end(self):
            self.ended += 1
            super().on_decision_end()

    policy = ProbeProvider()
    try:
        result = Runner(store, config, FakeGame(), policy).run()
        assert result["committed_actions"] == 1
        assert result["reason"] == "AGENT_ABORT"
        assert policy.ended == 1
    finally:
        policy.client.close()
