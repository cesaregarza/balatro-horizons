"""Provider transport mechanics for the episode harness."""

import uuid

from balatro_horizons.harness.contract import ProviderPolicy
from balatro_horizons.harness.money import BudgetExhausted, reservation_usd
from balatro_horizons.harness.transport import ProviderFailure


class OperatorAbort(RuntimeError):
    """Signal that an operator requested the current episode to stop."""


class ProviderRuntimeMixin:
    """Supply metered provider requests to the decision runtime."""

    def _provider(self, policy: ProviderPolicy, ctx, exchanges):
        body = policy.request(ctx, exchanges)
        self._check_provider_budget()
        self._log_provider_input(policy, body)
        reserve = reservation_usd(policy.model, self.limits)
        for attempt in range(self.limits.max_transport_attempts):
            request_id = self._reserve_provider_attempt(body, reserve, attempt)
            try:
                response = policy.send(body)
            except ProviderFailure as error:
                if self._handle_provider_failure(error, request_id, attempt):
                    continue
                raise
            return self._settle_provider_response(policy, response, reserve, request_id)

    def _check_provider_budget(self):
        if self.stop.is_set():
            raise OperatorAbort
        if self.calls >= self.limits.max_provider_calls:
            raise BudgetExhausted("PROVIDER_CALL_LIMIT")

    def _log_provider_input(self, policy, body):
        input_measurement = policy.check_input(body)
        if input_measurement is not None:
            self.log(
                "provider_input_check",
                input_measurement,
                observation_id=self.observation.observation_id,
            )

    def _reserve_provider_attempt(self, body, reserve, attempt):
        self._check_provider_budget()
        request_id = uuid.uuid4().hex
        self.spending.reserve(
            request_id,
            self.eid,
            reserve,
            self.limits.max_episode_cost_usd,
            prior_cost=self.prior_cost,
        )
        self.calls += 1
        self.cost += reserve
        self.log(
            "provider_reservation",
            {"reserved_usd": reserve, "attempt": attempt + 1},
            actor="agent",
            request_id=request_id,
            observation_id=self.observation.observation_id,
        )
        self.log(
            "provider_request",
            {"body": body, "reserved_usd": reserve, "attempt": attempt + 1},
            actor="agent",
            request_id=request_id,
            observation_id=self.observation.observation_id,
        )
        return request_id

    def _handle_provider_failure(self, error, request_id, attempt):
        self.spending.retain(request_id)
        self.log(
            "provider_error",
            {"code": error.code, "provider_code": error.provider_code, "usage": "unknown"},
            request_id=request_id,
        )
        if not error.retryable or attempt + 1 == self.limits.max_transport_attempts:
            return False
        if self.stop.wait(min(2**attempt, 4)):
            raise OperatorAbort from None
        return True

    def _settle_provider_response(self, policy, response, reserve, request_id):
        actual = policy.usage_cost(response, reserve)
        self.spending.settle(request_id, actual)
        self.cost += actual - reserve
        self.log(
            "provider_response",
            {"body": response, "cost_usd": actual},
            actor="agent",
            request_id=request_id,
        )
        return policy.parse(response)
