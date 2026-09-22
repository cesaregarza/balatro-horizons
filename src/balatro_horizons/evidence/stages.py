"""The declarative native-evidence plan used by the evidence commands.

The old release driver embedded four almost-identical lists of dictionaries.
This module keeps the plan data-only so it can be rendered without opening a
private manifest, reading a seed, or contacting a native runtime.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Stage:
    """One evidence stage and the artifact produced by its collector."""

    name: str
    launches: int
    game_resets: int
    reasons: tuple[str, ...]
    collector: str
    artifact: str

    def as_dict(self) -> dict:
        """Return the stable JSON representation used by ``evidence plan``."""
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


_FUNCTIONAL_CASES = (
    "invalid action",
    "shop action coverage",
    "terminal fixture",
    "ordinary WHITE run",
    "ordinary GOLD run",
    "reorder boundaries",
    "lost acknowledgment",
    "unknown action status (last)",
)

STARTUP_PROFILE_STABILITY = "startup profile stability"
FUNCTIONAL_COLLECTION = "functional collection"
RESTORATION_CERTIFICATION = "fresh-process restoration certification"
BRANCH_RESTORATION = "branch restoration"
GAMEPLAY_COLLECTION_ONLY = "gameplay collection only"
RESUMED_FUNCTIONAL_COLLECTION = "resumed functional collection"
REORDER_ACCEPTANCE_FIXTURE = "reorder acceptance fixture"


def _full_stages() -> tuple[Stage, ...]:
    return (
        Stage(
            STARTUP_PROFILE_STABILITY,
            2,
            4,
            ("two fresh-process profile repetitions",),
            "runtime",
            "runtime-audit-{stake}.json",
        ),
        Stage(
            FUNCTIONAL_COLLECTION,
            0,
            8,
            ("reuse of the second profile process", *_FUNCTIONAL_CASES),
            "acceptance",
            "native-fixtures-final.json",
        ),
        Stage(
            RESTORATION_CERTIFICATION,
            9,
            9,
            (
                "three fresh-process seed-prefix continuation probes for each of two episodes",
                "three fresh-process direct-checkpoint continuation probes",
            ),
            "certification",
            "certificate-record-*.json",
        ),
        Stage(
            BRANCH_RESTORATION,
            1,
            1,
            ("explicit child branch launch",),
            "certification",
            "native-release.json",
        ),
    )


def _gameplay_stages() -> tuple[Stage, ...]:
    return (
        Stage(
            GAMEPLAY_COLLECTION_ONLY,
            1,
            8,
            ("startup; one process reused across all eight gameplay cases", *_FUNCTIONAL_CASES),
            "acceptance",
            "native-gameplay-collection-*.json",
        ),
    )


def _resume_action_stages() -> tuple[Stage, ...]:
    return (
        Stage(
            RESUMED_FUNCTIONAL_COLLECTION,
            1,
            6,
            ("startup; one session reused across remaining cases",),
            "acceptance",
            "native-fixtures-final.json",
        ),
        *_full_stages()[2:],
    )


def _resume_certification_stages() -> tuple[Stage, ...]:
    return (
        Stage(
            REORDER_ACCEPTANCE_FIXTURE,
            1,
            1,
            ("startup for a standalone functional collection",),
            "acceptance",
            "native-reorder.json",
        ),
        *_full_stages()[2:],
    )


def stage_list(
    *, resume_certification: bool = False,
    resume_actions: bool = False,
    gameplay_only: bool = False,
    connection_only: bool = False,
    continuation_only: bool = False,
) -> tuple[Stage, ...]:
    """Select a non-executing plan, rejecting incompatible resume modes."""
    selected = sum((resume_certification, resume_actions, gameplay_only, connection_only, continuation_only))
    if selected > 1:
        raise ValueError("RESUME_MODES_ARE_MUTUALLY_EXCLUSIVE")
    if continuation_only:
        return (Stage(
            "initial blind continuation fixture", 4, 4,
            ("one unpaid fixture capture", "three fresh-process same-action restorations; stop on failure"),
            "bh evidence collect --continuation-only --report PATH", "native-continuation-*.json",
        ),)
    if connection_only:
        return (Stage(
            "Windows connection only", 1, 0,
            ("one owned calibration launch; registration refresh and RPC reopen in the same process",),
            "bh native diagnose --connection --report PATH", "native-connection-*.json",
        ),)
    if gameplay_only:
        return _gameplay_stages()
    if resume_certification:
        return _resume_certification_stages()
    if resume_actions:
        return _resume_action_stages()
    return _full_stages()


def plan(**options) -> dict:
    """Render stages plus compatibility totals from the former release plan."""
    stages = stage_list(**options)
    resets = sum(stage.game_resets for stage in stages)
    return {
        "stages": [stage.as_dict() for stage in stages],
        "expected_physical_launches": sum(stage.launches for stage in stages),
        "expected_game_resets": resets,
        "native_evidence_collected": False,
        "release_certification_requested": not (
            options.get("gameplay_only", False) or options.get("connection_only", False)
            or options.get("continuation_only", False)
        ),
        "capability_activation_requested": False,
    }


def from_stage(name: str, **options) -> tuple[Stage, ...]:
    """Return a plan suffix beginning at ``name`` for resumable collection."""
    if any(options.values()):
        stages = stage_list(**options)
    elif name == RESUMED_FUNCTIONAL_COLLECTION:
        stages = _resume_action_stages()
    elif name == REORDER_ACCEPTANCE_FIXTURE:
        stages = _resume_certification_stages()
    elif name == GAMEPLAY_COLLECTION_ONLY:
        stages = _gameplay_stages()
    else:
        stages = _full_stages()
    for index, stage in enumerate(stages):
        if stage.name == name:
            return stages[index:]
    raise ValueError("UNKNOWN_EVIDENCE_STAGE")
