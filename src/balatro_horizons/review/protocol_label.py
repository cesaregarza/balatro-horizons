"""Bounded historical harness label lookup, without replay or a journal scan."""

import json

from balatro_horizons.storage.journal import digest


def protocol_label(store, eid):
    """Use only a hash-valid genesis reference and its matching frozen bundle.

    This is display metadata, not certification of the remaining journal. Missing
    or unusual old provenance stays unknown rather than causing an unbounded read.
    """
    try:
        with (store.episode_path(eid) / "events.jsonl").open("rb") as stream:
            line = stream.readline(64 * 1024 + 1)
        if len(line) > 64 * 1024 or not line.endswith(b"\n"):
            return None
        event = json.loads(line)
        expected = event.pop("hash")
        if (event.get("sequence") != 0 or event.get("previous_hash") != "0" * 64
                or event.get("episode_id") != eid or event.get("type") != "episode_start"
                or digest(event) != expected):
            return None
        reference = event["payload"]["agent_protocol"]
        path = store.episode_path(reference["episode_id"], True) / "agent-protocol.json"
        with path.open("rb") as stream:
            content = stream.read(256 * 1024 + 1)
        if len(content) > 256 * 1024:
            return None
        protocol = json.loads(content)
        label = protocol.get("interface")
        if (digest(protocol) == reference["hash"] and isinstance(label, str)
                and 0 < len(label) <= 64 and label.isprintable()):
            return label
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        pass
    return None
