"""A frozen, public skill library. Reads never open model-selected filesystem paths."""

import json
import re
from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from balatro_horizons.config import (
    DEFAULT_GUIDE_PAGE_BYTES,
    ROOT,
    SKILL_DESCRIPTION_PREVIEW_CHARACTERS,
)
from balatro_horizons.storage.journal import digest


class SkillInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^balatro-[a-z0-9-]+$", max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    key: str = Field(pattern=r"^guide/balatro-[a-z0-9-]+$")


def load_guide(path=None):
    path = Path(path or ROOT / "docs/balatro-guide/rules.json")
    if path.stat().st_size > 2_000_000:
        raise ValueError("GUIDE_TOO_LARGE")
    guide = json.loads(path.read_text())
    if not isinstance(guide, dict):
        raise ValueError("INVALID_GUIDE_LIBRARY")
    expected = guide.pop("bundle_hash", None)
    if expected != digest(guide) or guide.get("format") != "balatro-horizons-guide-v1":
        raise ValueError("GUIDE_INTEGRITY_FAILURE")
    entries = guide.get("entries")
    aliases = guide.get("aliases")
    if not isinstance(entries, dict) or not isinstance(aliases, dict):
        raise ValueError("INVALID_GUIDE_LIBRARY")
    for key, value in entries.items():
        if not re.fullmatch(r"guide/[a-z0-9/-]+", key) or not isinstance(value, str):
            raise ValueError("INVALID_GUIDE_ENTRY")
    if not all(
        isinstance(k, str) and isinstance(v, str) and v in entries for k, v in aliases.items()
    ):
        raise ValueError("INVALID_GUIDE_ALIAS")
    skills = [SkillInfo.model_validate(item).model_dump() for item in guide.get("skills", [])]
    if not skills or len({item["name"] for item in skills}) != len(skills):
        raise ValueError("INVALID_SKILL_CATALOG")
    if any(item["key"] != "guide/" + item["name"] or item["key"] not in entries for item in skills):
        raise ValueError("INVALID_SKILL_TARGET")
    return guide, skills, expected


def prepare_rules(rules, preset, path=None):
    rules = deepcopy(rules)
    if preset == "none":
        return rules
    guide, skills, bundle_hash = load_guide(path)
    entries = deepcopy(rules.get("entries", rules))
    aliases = deepcopy(rules.get("aliases", {}))
    if set(entries) & set(guide["entries"]) or set(aliases) & set(guide["aliases"]):
        raise ValueError("GUIDE_RULES_COLLISION")
    result = deepcopy(rules) if "entries" in rules else {}
    result.update(
        entries={**entries, **guide["entries"]},
        aliases={**aliases, **guide["aliases"]},
        skills=skills,
        guide={
            "preset": preset,
            "bundle_hash": bundle_hash,
            "content_hash": guide["guide_content_sha256"],
            "protocol": "skills_v1",
        },
    )
    return result


def discovery(skills, interface, *, descriptions=True):
    if not skills:
        return ""
    if not descriptions:
        if interface in ("tools_v2", "tools_v3", "tools_v4", "tools_v5"):
            return "\n\nRead Balatro skills with read_skill(name); linked chapters use read_rules(key)."
        names = ", ".join(item["name"] for item in skills)
        return (
            "\n\nRead skills with a rules operation using guide/<name>. Available names: " + names
        )
    command = (
        "read_skill(name)"
        if interface in ("tools_v2", "tools_v3", "tools_v4", "tools_v5")
        else "a rules operation with key guide/<name>"
    )
    if interface in ("tools_v3", "tools_v4", "tools_v5"):
        rows = "\n".join(
            f"- {item['name']}: {item['description'][:SKILL_DESCRIPTION_PREVIEW_CHARACTERS]}"
            + ("…" if len(item["description"]) > SKILL_DESCRIPTION_PREVIEW_CHARACTERS else "")
            for item in skills
        )
        return (
            "\n\nAvailable skills (description previews):\n"
            + rows
            + "\nRead with read_skill(name); follow linked chapters with read_rules(key). "
            "Save useful facts or keys in your notes for later decisions."
        )
    rows = "\n".join(f"- {item['name']}: {item['description']}" for item in skills)
    return (
        "\n\nAvailable Balatro skills (names and descriptions only):\n"
        + rows
        + "\nRead a relevant skill with "
        + command
        + " before acting when useful. "
        "Load only the chapters needed; referenced details use read_rules(key). "
        "Reading consumes a helper call and does not advance the game. "
        "Skill text remains in this decision's tool exchanges. At the next game decision, "
        "only your explicit memory carries forward; save concise notes or chapter keys there."
    )


def restore_knowledge(store, checkpoint):
    """Verify the immutable parent library before accepting a continuation."""
    reference = checkpoint.get("knowledge")
    if not isinstance(reference, dict) or not isinstance(reference.get("episode_id"), str):
        raise ValueError("KNOWLEDGE_SNAPSHOT_MISSING")
    path = store.episode_path(reference["episode_id"], True) / "knowledge.json"
    try:
        rules = json.loads(path.read_text())
    except FileNotFoundError:
        raise ValueError("KNOWLEDGE_SNAPSHOT_MISSING") from None
    if not isinstance(rules, dict) or digest(rules) != reference.get("hash"):
        raise ValueError("KNOWLEDGE_SNAPSHOT_MISMATCH")
    return rules


def read_guide(rules, requested_key, max_bytes=DEFAULT_GUIDE_PAGE_BYTES):
    """Return an explicit page of one immutable entry; continuation keys are public."""
    key, separator, offset_text = requested_key.partition("#offset=")
    if separator and not re.fullmatch(r"[0-9]{1,7}", offset_text):
        raise ValueError("INVALID_GUIDE_OFFSET")
    offset = int(offset_text) if separator else 0
    key = rules.get("aliases", {}).get(key.casefold(), key)
    entry = rules.get("entries", {}).get(key)
    if not isinstance(entry, str) or not key.startswith("guide/") or offset > len(entry):
        raise ValueError("UNKNOWN_GUIDE_ENTRY")
    page = entry[offset:].encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    end = offset + len(page)
    return {
        "reference": "balatro_guide",
        "key": key,
        "entry": page,
        "entry_hash": digest(entry),
        "offset": offset,
        "next_key": f"{key}#offset={end}" if end < len(entry) else None,
        "complete": end == len(entry),
        "game_advanced": False,
    }
