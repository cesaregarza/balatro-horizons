"""Immutable episode protocol; prompts are data, executable changes require revalidation."""

import hashlib
import json
from copy import deepcopy

from balatro_horizons.agents.instructions import load_prompt
from balatro_horizons.config import RECENT_PUBLIC_EVENT_LIMIT, ROOT
from balatro_horizons.engine.provenance import implementation_fingerprint
from balatro_horizons.storage.journal import digest


def episode_limits(config):
    # Operator permission and shared campaign funding are not agent allowances.
    return config.budgets.model_dump(exclude={"paid_calls_enabled", "max_batch_cost_usd"})


def freeze_protocol(config, policy, rules, *, prompt_bytes=None):
    from balatro_horizons.agents.focused import PAGE_BYTES, RETAINED_RESULTS, focused_tools
    from balatro_horizons.agents.protocol import KERNEL, TOOL
    from balatro_horizons.agents.skills import discovery
    from balatro_horizons.agents.tool_interface import (
        CONTINUATION_INTERFACES,
        FOCUSED_INTERFACES,
        NOTEBOOK_INTERFACE,
        stable_tools,
    )

    interface = getattr(policy, "interface", "operate_v1")
    raw = load_prompt(ROOT, interface) if prompt_bytes is None else prompt_bytes
    skills = rules.get("skills", [])
    kernel = (
        "Resolve scores in native order. Read the available skills and linked rules when useful."
        if skills
        else KERNEL
    )
    tools = stable_tools(skills=skills)
    if interface in FOCUSED_INTERFACES:
        tools = focused_tools(tools)
    if interface == NOTEBOOK_INTERFACE:
        from balatro_horizons.agents.notebook import notebook_tools

        tools = notebook_tools(tools)
    model = getattr(policy, "model", None)
    return {
        "version": "agent-protocol-v1",
        "interface": interface,
        "prompt_utf8": raw.decode("utf-8"),
        "prompt_sha256": hashlib.sha256(raw).hexdigest(),
        "rules_kernels": {
            str(descriptions): kernel + discovery(skills, interface, descriptions=descriptions)
            for descriptions in (True, False)
        },
        "tool": deepcopy(TOOL),
        "tool_catalog": tools,
        "tool_policy": "stable_catalog_local_phase_rejection"
        if interface in CONTINUATION_INTERFACES
        else "versioned_legacy_" + interface,
        "model": model.model_dump() if model is not None else None,
        "agent": getattr(policy, "name", "model"),
        "benchmark": deepcopy(config.benchmark),
        "episode_limits": episode_limits(config),
        "knowledge_hash": digest(rules),
        "skills_preset": config.skills,
        "memory_policy": {
            "across_actions": "run-notebook-v1" if interface == NOTEBOOK_INTERFACE else "explicit_memory_only",
            "recent_public_events": RECENT_PUBLIC_EVENT_LIMIT,
            "retained_results": RETAINED_RESULTS if interface in FOCUSED_INTERFACES else None,
            "page_bytes": PAGE_BYTES if interface in FOCUSED_INTERFACES else None,
            "provider_continuation": "within_decision_only" if interface in CONTINUATION_INTERFACES else "none",
            "context_bound": "request_bytes_and_provider_tokens_v2",
            **({"notebook_characters": "sum_unicode_key_and_text_lengths",
                "note_writes": "journaled_helpers",
                "branch_boundary": "pre_decision",
                "helper_exhaustion": "bounded_invalid_feedback"}
               if interface == NOTEBOOK_INTERFACE else {}),
        },
        "public_export_policy": "public-schema-v1-opaque-continuations-omitted",
        "implementation_hash": implementation_fingerprint(),
    }


def restore_protocol(store, checkpoint):
    reference = checkpoint.get("agent_protocol")
    if not isinstance(reference, dict):
        raise ValueError("AGENT_PROTOCOL_SNAPSHOT_MISSING")
    try:
        bundle = json.loads(
            (store.episode_path(reference["episode_id"], True) / "agent-protocol.json").read_text()
        )
    except (FileNotFoundError, KeyError):
        raise ValueError("AGENT_PROTOCOL_SNAPSHOT_MISSING") from None
    if digest(bundle) != reference.get("hash") or bundle.get("version") != "agent-protocol-v1":
        raise ValueError("AGENT_PROTOCOL_SNAPSHOT_MISMATCH")
    if bundle["implementation_hash"] != implementation_fingerprint():
        raise ValueError("AGENT_PROTOCOL_IMPLEMENTATION_CHANGED")
    return bundle


def validate_continuation(bundle, config, agent, *, human=False):
    if (
        bundle["episode_limits"] != episode_limits(config)
        or bundle["benchmark"] != config.benchmark
    ):
        raise ValueError("AGENT_PROTOCOL_CONFIGURATION_CHANGED")
    if human:
        return
    model = config.models.get(agent)
    current = model.model_dump() if model is not None else None
    if bundle["model"] != current or (current is None and bundle["agent"] != agent):
        raise ValueError("AGENT_PROTOCOL_MODEL_CHANGED")
