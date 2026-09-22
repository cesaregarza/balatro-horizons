"""Summarize private replay divergence without exposing private state."""

import json
from collections.abc import Iterator
from pathlib import Path


def differing_paths(left, right, path: str = "") -> Iterator[tuple[str, str]]:
    """Yield only structural/value locations, never the differing values."""
    if type(left) is not type(right):
        yield path, "type"
    elif isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            location = path + "." + str(key)
            if key not in left or key not in right:
                yield location, "presence"
            else:
                yield from differing_paths(left[key], right[key], location)
    elif isinstance(left, list):
        if len(left) != len(right):
            yield path, "length"
        for index, (expected, actual) in enumerate(zip(left, right, strict=False)):
            yield from differing_paths(expected, actual, f"{path}[{index}]")
    elif left != right:
        yield path, "value"


def inspect_artifact(path: Path, limit: int = 20) -> dict:
    """Return a bounded, value-free divergence summary."""
    if limit < 0:
        raise ValueError("INVALID_DIFFERENCE_LIMIT")
    try:
        data = json.loads(Path(path).read_text(encoding="utf8"))
        differences = list(differing_paths(data["expected"], data["actual"]))
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("INVALID_DIVERGENCE_ARTIFACT") from error
    return {
        "decision": data.get("decision"),
        "difference_count": len(differences),
        "first_differences": differences[:limit],
    }
