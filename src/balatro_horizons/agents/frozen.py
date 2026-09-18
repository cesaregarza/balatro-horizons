"""Immutable episode protocol; prompts are data, executable changes require revalidation."""

import hashlib
import json
from copy import deepcopy

from balatro_horizons.agents.instructions import load_prompt
from balatro_horizons.config import RECENT_PUBLIC_EVENT_LIMIT, ROOT
from balatro_horizons.engine.provenance import implementation_fingerprint
from balatro_horizons.storage.journal import digest

FROZEN_INTERFACE = "tools_v7"


def episode_limits(config):
    # Operator permission and shared campaign funding are not agent allowances.
    return config.budgets.model_dump(exclude={"paid_calls_enabled", "max_batch_cost_usd"})


def freeze_protocol(config, policy, rules, *, prompt_bytes=None):
    from balatro_horizons.agents.focused import PAGE_BYTES, RETAINED_RESULTS, focused_tools
    from balatro_horizons.agents.notebook import notebook_tools
    from balatro_horizons.agents.protocol import KERNEL
    from balatro_horizons.agents.skills import discovery
    from balatro_horizons.agents.tool_interface import stable_tools

    raw = load_prompt(ROOT) if prompt_bytes is None else prompt_bytes
    skills = rules.get("skills", [])
    kernel = (
        "Resolve scores in native order. Read the available skills and linked rules when useful."
        if skills
        else KERNEL
    )
    from balatro_horizons.agents.outcomes import VERSION as outcome_version
    tools = notebook_tools(
        focused_tools(stable_tools(skills=skills, target_guidance=True)), action_notes=True
    )
    from balatro_horizons.agents.working_memory import policy as working_memory_policy
    model = getattr(policy, "model", None)
    return {
        "version": "agent-protocol-v1",
        "interface": FROZEN_INTERFACE,
        "prompt_utf8": raw.decode("utf-8"),
        "prompt_sha256": hashlib.sha256(raw).hexdigest(),
        "rules_kernel": kernel + discovery(skills),
        "tool": None,
        "tool_catalog": tools,
        "tool_policy": "stable_catalog_local_phase_rejection",
        "model": model.model_dump() if model is not None else None,
        "agent": getattr(policy, "name", "model"),
        "benchmark": deepcopy(config.benchmark),
        "episode_limits": episode_limits(config),
        "knowledge_hash": digest(rules),
        "skills_preset": config.skills,
        "memory_policy": {
            "across_actions": "run-notebook-v1-and-" + working_memory_policy()["version"],
            "recent_public_events": RECENT_PUBLIC_EVENT_LIMIT,
            "retained_results": RETAINED_RESULTS,
            "page_bytes": PAGE_BYTES,
            "provider_continuation": "within_decision_only",
            "context_bound": "request_bytes_and_provider_tokens_v2",
            "notebook_characters": "sum_unicode_key_and_text_lengths",
            "branch_boundary": "pre_decision",
            "helper_exhaustion": "bounded_invalid_feedback",
            "action_outcome": outcome_version,
            "working_memory": working_memory_policy(),
            "notebook_guidance": "evidence_backed_corrections_with_pre_eviction_notice",
            "note_writes": "journaled_helpers_or_validated_action_attachment",
        },
        "public_export_policy": "public-schema-v1-opaque-continuations-omitted",
        "implementation_hash": implementation_fingerprint(),
    }


def read_protocol(store, checkpoint):
    """Read hash-checked frozen data without accepting it for execution."""
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
    if bundle.get("interface") != FROZEN_INTERFACE:
        raise ValueError("AGENT_PROTOCOL_INTERFACE_RETIRED")
    return bundle


def restore_protocol(store, checkpoint):
    bundle = read_protocol(store, checkpoint)
    reference = checkpoint["agent_protocol"]
    extension = checkpoint.get("budget_extension")
    if extension is not None:
        from balatro_horizons.agents.budget import validate_caps

        cap = extension.get("combined_cap_usd")
        validate_caps(cap, cap)
        if (extension.get("version") != "budget-extension-v1"
                or extension.get("parent_protocol") != reference
                or extension.get("recorded_implementation_hash") != bundle["implementation_hash"]
                or extension.get("implementation_hash") != implementation_fingerprint()
                or cap <= bundle["episode_limits"]["max_episode_cost_usd"]):
            raise ValueError("BUDGET_EXTENSION_PROTOCOL_MISMATCH")
        # A deliberate intervention creates a new protocol. Original snapshots,
        # prompts, tools and model settings remain immutable. Source transitions
        # are explicit; ordinary branches still reject changed implementations.
        bundle["episode_limits"]["max_episode_cost_usd"] = cap
        bundle["implementation_hash"] = implementation_fingerprint()
        bundle["budget_extension"] = deepcopy(extension)
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
