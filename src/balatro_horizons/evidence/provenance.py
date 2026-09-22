"""Source and private continuation fingerprints used to invalidate stale evidence."""

import hashlib

from balatro_horizons.config import ROOT
from balatro_horizons.storage.journal import digest

IMPLEMENTATION_FILES = (
    "config.py",
    "contracts.py",
    "service.py",
    "service_execution.py",
    "workbench/branches.py",
    "workbench/policies.py",
    "workbench/budget_continuation.py",
    "workbench/budget_ledger.py",
)
IMPLEMENTATION_DIRECTORIES = (
    "game", "evidence", "observations", "actions", "storage", "harness"
)
NATIVE_COMPONENT_DIRECTORIES = ("game", "observations", "actions", "storage")
NATIVE_COMPONENT_FILES = ("evidence/continuation_probe.py",)


def source_files(root=ROOT):
    base = root / "src/balatro_horizons"
    return {str(path.relative_to(root)): path.read_bytes()
            for path in base.rglob("*.py") if path.is_file()}


def fingerprint_sources(sources):
    """Reproduce the full identity from source bytes without executing historical code."""
    prefix = "src/balatro_horizons/"
    return digest({
        name: hashlib.sha256(content).hexdigest()
        for name, content in sources.items()
        if name.startswith(prefix) and (
            name[len(prefix):] in IMPLEMENTATION_FILES
            or name[len(prefix):].split("/")[0] in IMPLEMENTATION_DIRECTORIES
        )
    })


def native_component_manifest(sources=None):
    """Return the per-file native identity held constant by a reuse receipt."""
    sources = source_files(ROOT) if sources is None else sources
    prefix = "src/balatro_horizons/"
    excluded = {"game/fake.py"}
    return {
        name: hashlib.sha256(content).hexdigest()
        for name, content in sources.items()
        if name.startswith(prefix)
        and name.endswith(".py")
        and (
            name[len(prefix):] == "contracts.py"
            or name[len(prefix):].split("/")[0] in NATIVE_COMPONENT_DIRECTORIES
            or name[len(prefix):] in NATIVE_COMPONENT_FILES
        )
        and name[len(prefix):] not in excluded
    }


def native_implementation_fingerprint(sources=None):
    """Hash the native contract and execution/visibility/storage files."""
    return digest(native_component_manifest(sources))


def accepted_source_matches(record):
    """Reuse never grants blanket approval to unreviewed future harness changes."""
    if "native_implementation_hash" in record:
        return (record.get("native_implementation_hash") == native_implementation_fingerprint()
                and record.get("accepted_implementation_hash") == implementation_fingerprint())
    return record.get("implementation_hash") == implementation_fingerprint()


def implementation_fingerprint():
    base = ROOT / "src/balatro_horizons"
    # Full source acceptance covers native execution and the harness that prepares
    # model context. Native-component identity is checked separately below.
    paths = [base / name for name in IMPLEMENTATION_FILES]
    for directory in IMPLEMENTATION_DIRECTORIES:
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
