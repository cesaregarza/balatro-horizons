"""Shared public export projection and privacy checks."""

import json
import re

# Require a drive-letter boundary so a public https:// link is not treated as s:/.
FORBIDDEN = re.compile(
    r"(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9_-]+|/mnt/|/root/|"
    r"(?<![A-Za-z0-9+.-])[A-Za-z]:[\\/]|rng_state|hidden_draw_order|PRIVATE KEY)"
)


def scan(value, secrets=()):
    serialized = json.dumps(value, ensure_ascii=False)
    if FORBIDDEN.search(serialized) or any(secret and secret in serialized for secret in secrets):
        raise ValueError("EXPORT_PRIVACY_SCAN_FAILED")


def public_provider_payload(value):
    """Remove opaque continuation material while retaining inspectable public output."""
    if isinstance(value, list):
        return [public_provider_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    block_type = value.get("type")
    result = {
        key: public_provider_payload(item)
        for key, item in value.items()
        if key != "encrypted_content"
        and not (block_type in ("thinking", "redacted_thinking") and key == "signature")
        and not (block_type == "redacted_thinking" and key == "data")
    }
    omitted = (
        "encrypted_content" in value
        or (block_type in ("thinking", "redacted_thinking") and "signature" in value)
        or (block_type == "redacted_thinking" and "data" in value)
    )
    if omitted:
        result["opaque_continuation_omitted"] = True
    return result
