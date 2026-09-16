"""Explicit public diagnostics; exception messages and locals are never exported."""


class HarnessFailure(ValueError):
    CODES = {
        "LOCAL_CONTEXT_LIMIT",
        "INPUT_TOKEN_LIMIT",
        "TOKEN_COUNT_UNAVAILABLE",
        "AGENT_PROTOCOL_IMPLEMENTATION_CHANGED",
        "PERSISTENT_INSTRUCTIONS_STALE",
        "PERSISTENT_INSTRUCTIONS_INVALID",
    }

    def __init__(self, code, *, stage="context", **measurements):
        if code not in self.CODES:
            raise ValueError("Unknown harness failure code")
        if stage not in {
            "context",
            "initial_request",
            "helper_followup",
            "input_token_count",
            "protocol_freeze",
            "decision_start",
            "request",
        }:
            raise ValueError("Unknown failure stage")
        allowed = {
            "request_bytes",
            "byte_limit",
            "input_tokens",
            "token_limit",
            "token_margin",
            "retained_provider_turns",
            "retained_helper_results",
            "http_status",
        }
        if any(k not in allowed or type(v) is not int or v < 0 for k, v in measurements.items()):
            raise ValueError("Invalid diagnostic measurement")
        self.code = code
        self.details = {"stage": stage, **measurements}
        super().__init__(code)

    def public(self):
        return {"code": self.code, **self.details}
