"""Explicit spending construction for synthetic runner tests."""

from balatro_horizons.harness.money import Spending


def episode_spending(store, config):
    """Return the named episode-only ledger used by one synthetic test store."""
    return Spending.episode_only(
        store.root / "private_runs" / "test-spending.json",
        config.budgets.max_episode_cost_usd or 1,
    )
