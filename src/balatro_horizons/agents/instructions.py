"""One renderer for editing and new-episode prompt admission."""

from balatro_horizons.agents.failures import HarnessFailure
from balatro_horizons.config import ALWAYS_LOADED_MAX_BYTES

BEGIN = "<!-- BEGIN ALWAYS-LOADED.md -->"
END = "<!-- END ALWAYS-LOADED.md -->"


def render(prompt: str, instructions: str) -> str:
    if not instructions.strip() or len(instructions.encode("utf-8")) > ALWAYS_LOADED_MAX_BYTES:
        raise ValueError(f"ALWAYS-LOADED.md must contain 1-{ALWAYS_LOADED_MAX_BYTES} UTF-8 bytes")
    if BEGIN in instructions or END in instructions:
        raise ValueError("Instructions must not contain generated-block markers")
    block = BEGIN + "\n" + instructions.strip() + "\n" + END
    if BEGIN not in prompt and END not in prompt:
        return prompt.rstrip() + "\n\n" + block + "\n"
    if prompt.count(BEGIN) != 1 or prompt.count(END) != 1:
        raise ValueError("Prompt must contain exactly one complete generated block")
    before, _, remainder = prompt.partition(BEGIN)
    if END not in remainder:
        raise ValueError("Generated-block markers are out of order")
    _, _, after = remainder.partition(END)
    return before + block + after


def load_prompt(root, interface):
    filename = "core.txt" if interface == "operate_v1" else interface.replace("_", "-") + ".txt"
    raw = (root / "configs/prompts" / filename).read_bytes()
    if interface == "tools_v5":
        try:
            source = (root / "configs/prompts/ALWAYS-LOADED.md").read_text(encoding="utf-8")
            rendered = render(raw.decode("utf-8"), source).encode("utf-8")
        except (OSError, ValueError):
            raise HarnessFailure(
                "PERSISTENT_INSTRUCTIONS_INVALID", stage="protocol_freeze"
            ) from None
        if rendered != raw:
            raise HarnessFailure("PERSISTENT_INSTRUCTIONS_STALE", stage="protocol_freeze")
    return raw
