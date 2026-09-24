"""Terminal accounting categories shared by execution, review and budget ledgers."""

RECOVERY_REASONS = frozenset({"PROCESS_INTERRUPTED", "AMBIGUOUS_ACTION_AFTER_CRASH"})
INCOMPLETE_TERMINAL_REASON = "EXECUTION_TERMINAL_INCOMPLETE"
GAME_TERMINAL_OUTCOMES = frozenset({"WIN", "GAME_LOSS"})
