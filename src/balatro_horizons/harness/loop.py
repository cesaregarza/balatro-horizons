"""Named-phase episode loop and the public constructor for one run."""

from balatro_horizons.harness.provider import OperatorAbort
from balatro_horizons.harness.runtime import Runner as _RuntimeRunner


class Runner(_RuntimeRunner):
    """Named-phase loop whose ledger is supplied by every caller."""

    def run(self, *, eid=None, manifest=None, private=None, resume=None, history_prefix=None):
        self.resume = resume
        self.freeze_or_restore_protocol(resume)
        self.bind_ledger()
        self.create_episode(eid=eid, manifest=manifest, private=private, history_prefix=history_prefix)
        self.rehydrate_resume_state(resume)
        outcome, reason, cost_context = (
            "INFRASTRUCTURE_FAILURE",
            "UNEXPECTED_RUNNER_FAILURE",
            None,
        )
        try:
            self._play_result = self.play()
        except Exception as error:
            outcome, reason, cost_context = self.classify_exit(error)
        else:
            outcome, reason, cost_context = self.classify_exit(None)
        finally:
            self.finish(outcome, reason, cost_context)
        return self.store.summary(self.eid)


def run_episode(store, config, game, policy, spending, *, stop=None, rules=None, prompt_bytes=None,
                eid=None, manifest=None, private=None, resume=None, history_prefix=None):
    """Run one episode with an explicit campaign or episode-scoped ledger."""
    loop = Runner(
        store,
        config,
        game,
        policy,
        spending,
        stop=stop,
        rules=rules,
        prompt_bytes=prompt_bytes,
    )
    return loop.run(eid=eid, manifest=manifest, private=private, resume=resume,
                    history_prefix=history_prefix)


__all__ = ["OperatorAbort", "Runner", "run_episode"]
