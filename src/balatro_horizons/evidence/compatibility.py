"""Explicit source compatibility for restoration, without rewriting old identities."""

import hashlib
import json
import subprocess

from balatro_horizons.config import ROOT
from balatro_horizons.evidence.execution_identity import execution_manifest
from balatro_horizons.evidence.provenance import (
    fingerprint_sources,
    implementation_fingerprint,
    native_component_manifest,
    source_files,
)
from balatro_horizons.evidence.reuse import certified_revision, revision_sources
from balatro_horizons.storage.journal import digest


def _identity(sources, *, game_kind, historical=False):
    native = native_component_manifest(sources)
    if game_kind == "synthetic":
        fake = "src/balatro_horizons/game/fake.py"
        native[fake] = hashlib.sha256(sources[fake]).hexdigest()
    return {"native_hash": digest(native),
            "execution_hash": digest(execution_manifest(sources, historical=historical))}


def prepare_compatibility(source_hashes, protocol, *, game_kind):
    """Read-only proof against immutable Git source; missing provenance refuses."""
    if game_kind not in ("native", "synthetic"):
        raise ValueError("CHECKPOINT_KIND_UNSUPPORTED")
    current = implementation_fingerprint()
    previous = set(source_hashes) | {protocol["implementation_hash"]}
    previous.discard(current)
    if not previous:
        return None
    identity = _identity(source_files(ROOT), game_kind=game_kind)
    revisions = {}
    for source in sorted(previous):
        try:
            revision = certified_revision(ROOT, source)
            _, historical = _historical_sources(revision)
        except (subprocess.CalledProcessError, OSError, ValueError):
            raise ValueError("RESTORE_SOURCE_HISTORY_UNAVAILABLE") from None
        if (fingerprint_sources(historical) != source
                or _identity(historical, game_kind=game_kind, historical=True) != identity):
            raise ValueError("RESTORE_SOURCE_INCOMPATIBLE")
        revisions[source] = revision
    return {"version": "restore-source-v1", "game_kind": game_kind,
            "accepted_implementation_hash": current,
            "protocol_hash": digest(protocol), "source_revisions": revisions, **identity}


def validate_compatibility(record):
    if record is None:
        return
    if not isinstance(record, dict):
        raise ValueError("RESTORE_COMPATIBILITY_INVALID")
    current = implementation_fingerprint()
    if (record.get("version") != "restore-source-v1"
            or record.get("accepted_implementation_hash") != current):
        raise ValueError("RESTORE_COMPATIBILITY_STALE")
    game_kind = record.get("game_kind")
    if game_kind not in ("native", "synthetic"):
        raise ValueError("RESTORE_COMPATIBILITY_INVALID")
    identity = _identity(source_files(ROOT), game_kind=game_kind)
    if any(record.get(key) != value for key, value in identity.items()):
        raise ValueError("RESTORE_SOURCE_INCOMPATIBLE")
    revisions = record.get("source_revisions")
    if not isinstance(revisions, dict) or not revisions:
        raise ValueError("RESTORE_COMPATIBILITY_INVALID")
    for source, revision in revisions.items():
        _, historical = _historical_sources(revision)
        if (fingerprint_sources(historical) != source
                or _identity(historical, game_kind=game_kind, historical=True) != identity):
            raise ValueError("RESTORE_SOURCE_INCOMPATIBLE")


def _historical_sources(revision):
    if (not isinstance(revision, str) or len(revision) != 40
            or any(char not in "0123456789abcdef" for char in revision)):
        raise ValueError("RESTORE_COMPATIBILITY_INVALID")
    try:
        return revision_sources(ROOT, revision)
    except (subprocess.CalledProcessError, OSError):
        raise ValueError("RESTORE_SOURCE_HISTORY_UNAVAILABLE") from None


def read_compatibility(store, checkpoint):
    reference = checkpoint.get("source_compatibility")
    if reference is None:
        return None
    try:
        record = json.loads((store.episode_path(reference["episode_id"], True)
                             / "source-compatibility.json").read_text())
        if digest(record) != reference["hash"]:
            raise ValueError("RESTORE_COMPATIBILITY_INVALID")
    except (KeyError, TypeError, FileNotFoundError):
        raise ValueError("RESTORE_COMPATIBILITY_MISSING") from None
    validate_compatibility(record)
    if record["game_kind"] != checkpoint["game"]["kind"]:
        raise ValueError("RESTORE_SOURCE_INCOMPATIBLE")
    return record


def require_protocol_compatibility(store, checkpoint, bundle):
    record = read_compatibility(store, checkpoint)
    if (record is None or record.get("protocol_hash") != digest(bundle)
            or bundle["implementation_hash"] not in record["source_revisions"]):
        raise ValueError("AGENT_PROTOCOL_IMPLEMENTATION_CHANGED")
