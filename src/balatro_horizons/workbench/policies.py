"""Human and intervention policies used by the optional workbench."""

import queue

from balatro_horizons.harness.loop import OperatorAbort


class HumanPolicy:
    interface = "tools_v8"
    name = "human"
    actor = "human"
    model = None

    def __init__(self, stop):
        self.queue = queue.Queue(maxsize=1)
        self.current = None
        self.stop = stop

    def decide(self, ctx, exchanges):
        self.current = {"context": ctx, "exchanges": exchanges}
        while not self.stop.is_set():
            try:
                result = self.queue.get(timeout=0.25)
                self.current = None
                return result
            except queue.Empty:
                pass
        raise OperatorAbort

    def on_decision_end(self) -> None:
        # The submitted operation already left the one-slot human queue.
        pass

    def on_commit(self) -> None:
        # A human selection has no provider continuation to advance.
        pass


class InterventionPolicy:
    interface = "tools_v8"

    def __init__(self, operations, continuation):
        self.operations = list(operations)
        self.continuation = continuation

    @property
    def actor(self):
        return "human_override" if self.operations else self.continuation.actor

    @property
    def active_policy(self):
        return self if self.operations else self.continuation

    @property
    def name(self):
        return self.continuation.name

    @property
    def model(self):
        return self.continuation.model

    def decide(self, ctx, exchanges):
        if self.operations:
            return {
                "kind": "action",
                "envelope": {
                    "observation_id": ctx.observation["observation_id"],
                    "action": self.operations.pop(0),
                },
            }
        return self.continuation.decide(ctx, exchanges)

    def on_decision_end(self):
        # An override itself has no continuation; forwarded turns have their own owner.
        pass

    def on_commit(self):
        # Removing the operation at decision time already advances this wrapper.
        pass


class HumanSequencePolicy:
    interface = "tools_v8"

    def __init__(self, human, continuation, steps):
        self.human, self.continuation, self.remaining = human, continuation, steps

    @property
    def active_policy(self):
        return self.human if self.remaining else self.continuation

    @property
    def name(self):
        return self.continuation.name

    @property
    def model(self):
        return self.continuation.model

    @property
    def actor(self):
        return "human" if self.remaining else self.continuation.actor

    def decide(self, ctx, exchanges):
        return (self.human if self.remaining else self.continuation).decide(ctx, exchanges)

    def on_commit(self):
        self.remaining = max(0, self.remaining - 1)

    def on_decision_end(self):
        # The active human/provider policy clears its own state before this wrapper advances.
        pass
