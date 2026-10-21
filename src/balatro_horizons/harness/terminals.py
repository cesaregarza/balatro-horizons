"""Terminal accounting categories shared by execution, review and budget ledgers."""

RECOVERY_REASONS = frozenset({"PROCESS_INTERRUPTED", "AMBIGUOUS_ACTION_AFTER_CRASH"})
INCOMPLETE_TERMINAL_REASON = "EXECUTION_TERMINAL_INCOMPLETE"
# Unclassified exceptions end with this code; their class stays in failure-diagnostic.json.
UNEXPECTED_ERROR_REASON = "HARNESS_UNEXPECTED_ERROR"
GAME_TERMINAL_OUTCOMES = frozenset({"WIN", "GAME_LOSS"})
