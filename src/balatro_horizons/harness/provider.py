"""Provider transport mechanics for the episode harness."""

import uuid

from balatro_horizons.harness.contract import ProviderPolicy
from balatro_horizons.harness.money import BudgetExhausted, reservation_usd
from balatro_horizons.harness.transport import ProviderFailure
from balatro_horizons.harness.transport.errors import MAX_RETRY_DELAY_SECONDS, InputCountFailure


class OperatorAbort(RuntimeError):
    """Signal that an operator requested the current episode to stop."""


class ProviderRuntimeMixin:
    """Supply metered provider requests to the decision runtime."""

    def _provider(self, policy: ProviderPolicy, ctx, exchanges):
        if bind_stop := getattr(policy, "bind_stop", None):
            bind_stop(self.stop.is_set)
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
        for attempt in range(self.limits.max_transport_attempts):
            self._check_provider_budget()
            try:
                input_measurement = policy.check_input(body)
                break
            except InputCountFailure as error:
                if not error.retryable or attempt + 1 == self.limits.max_transport_attempts:
                    raise
                self._wait_provider_retry(error, attempt)
            except ProviderFailure as error:
                if error.code == "PROVIDER_CANCELLED":
                    raise OperatorAbort from None
                raise
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
        try:
            self.spending.retain(request_id)
        except (KeyError, ValueError):
            # Preserve the provider failure if the reservation ledger is already inconsistent.
            pass
        self.log(
            "provider_error",
            {"code": error.code, "provider_code": error.provider_code, "usage": "unknown"},
            request_id=request_id,
        )
        if not error.retryable or attempt + 1 == self.limits.max_transport_attempts:
            if error.code == "PROVIDER_CANCELLED":
                raise OperatorAbort from None
            return False
        self._wait_provider_retry(error, attempt)
        return True

    def _wait_provider_retry(self, error, attempt):
        delay = error.retry_after
        if delay is None:
            delay = min(2**attempt, 4)
        if self.stop.wait(min(MAX_RETRY_DELAY_SECONDS, max(0, delay))):
            raise OperatorAbort from None

    def _settle_provider_response(self, policy, response, reserve, request_id):
        actual = policy.usage_cost(response, reserve)
        known = getattr(policy, "usage_known", lambda _response: True)(response)
        if known:
            self.spending.settle(request_id, actual)
            self.cost += actual - reserve
        else:
            self.spending.retain(request_id)
        payload = {"body": response, "cost_usd": actual} if known else {
            "body": response, "usage_known": False, "reserved_usd": reserve,
        }
        self.log(
            "provider_response",
            payload,
            actor="agent",
            request_id=request_id,
        )
        if not known:
            self.log("provider_error", {
                "code": "PROVIDER_USAGE_UNKNOWN", "provider_code": None, "usage": "unknown",
            }, request_id=request_id)
            raise ProviderFailure("PROVIDER_USAGE_UNKNOWN")
        return policy.parse(response)
