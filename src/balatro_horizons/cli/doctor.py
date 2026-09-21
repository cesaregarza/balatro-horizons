"""Native readiness diagnostics."""

import sys


def doctor(config, live=False):
    checks = base_checks(config)
    add_skills(checks, config)
    add_native(checks, config, live)
    return checks


def base_checks(config):
    return {
        "python": sys.version.split()[0],
        "offline_ready": True,
        "paid_enabled": config.budgets.paid_calls_enabled,
        "native_runtime": "missing",
        "native_certification": "blocked",
        "checkpoint_fidelity": "not_verified",
        "headless": "disabled_pending_parity",
        "blockers": [],
    }


def add_skills(checks, config):
    try:
        from balatro_horizons.agents.skills import prepare_rules

        knowledge = prepare_rules({}, config.skills)
        checks["skills"] = {
            "preset": config.skills,
            "available": len(knowledge.get("skills", [])),
            **knowledge.get("guide", {}),
        }
    except (OSError, ValueError) as error:
        checks["offline_ready"] = False
        checks["blockers"].append(str(error) if str(error).isupper() else "GUIDE_LOAD_FAILED")


def add_native(checks, config, live):
    from balatro_horizons.game.session import NativeFailure, WindowsBridge

    try:
        bridge = WindowsBridge(config.environment)
        lock = bridge.verify_files()
        checks["native_runtime"] = "installed"
        checks["game_version"] = lock["game_version"]
        from balatro_horizons.evidence.certification import require_environment_certificate

        try:
            certificate = require_environment_certificate(lock, config.environment)
            checks["native_certification"] = "passed"
            checks["checkpoint_fidelity"] = "per_decision_seed_prefix_certificates"
            checks["certified_phases"] = certificate.get("phases", [])
        except NativeFailure as error:
            checks["blockers"].append(str(error))
        if live:
            state = bridge.rpc("bh_inspect")
            bridge.verify_identity(state)
            checks["native_handshake"] = "passed"
    except (NativeFailure, OSError, ValueError) as error:
        checks["blockers"].append(str(error) if str(error).isupper() else type(error).__name__)
