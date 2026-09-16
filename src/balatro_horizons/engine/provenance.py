"""Source and private continuation fingerprints used to invalidate stale evidence."""

import hashlib

from balatro_horizons.config import ROOT
from balatro_horizons.storage.journal import digest


def implementation_fingerprint():
    base = ROOT / "src/balatro_horizons"
    # Native fidelity depends on the engine, projection, action policy, runner,
    # storage, and branch restoration. Presentation and report-only edits do not
    # invalidate native continuation evidence; their own tests cover those layers.
    paths = [
        base / name
        for name in (
            "config.py",
            "contracts.py",
            "runner.py",
            "service.py",
            "review/branches.py",
            "evaluation/scheduling.py",
        )
    ]
    for directory in ("engine", "observations", "actions", "agents", "storage"):
        paths.extend(sorted((base / directory).rglob("*.py")))
    # Prompt bytes are frozen into each episode's agent-protocol snapshot. They
    # cannot change engine execution or an existing continuation. Executable
    # agent code remains conservatively included because it shares validation,
    # budgeting and journaling dependencies with native execution.
    return digest(
        {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
            if p.is_file()
        }
    )


def continuation_fingerprint(state):
    raw = state.get("raw_engine")
    if raw is None:
        return digest(state)
    # These are native continuation fields, including ordered objects and RNG.
    # Transport readiness, paths, clocks and profile statistics are not run state.
    selected = {k: v for k, v in raw.items() if k not in ("bh",)}
    bh = raw.get("bh", {})
    selected["continuation"] = {
        k: bh.get(k)
        for k in (
            "rng",
            "tags",
            "pack_choices",
            "blind_on_deck",
            "credit_limit",
            "target",
            "deck_composition",
        )
    }
    return digest(selected)
