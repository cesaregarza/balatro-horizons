"""Reuse native evidence for an explicitly tested compatible harness.

This operation never reads Windows files, launches a game, calls a provider,
or migrates checkpoint certificates.  It prepares an immutable receipt and
only changes the active selection when the caller explicitly applies it.
"""

import io
import json
import subprocess
import tarfile
import uuid
from pathlib import Path

from balatro_horizons.evidence.lock import lock_digest, native_path
from balatro_horizons.evidence.provenance import (
    fingerprint_sources,
    native_component_manifest,
    native_implementation_fingerprint,
    source_files,
)
from balatro_horizons.evidence.validation import require
from balatro_horizons.storage.journal import atomic_json, digest, now


def revision_sources(root: Path, revision: str) -> tuple[str, dict[str, bytes]]:
    """Read Python source bytes from a committed revision without importing it."""
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "--verify", revision + "^{commit}"],
        text=True,
    ).strip()
    archive = subprocess.check_output(
        ["git", "-C", str(root), "archive", commit, "--", "src/balatro_horizons"]
    )
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        for member in contents:
            if member.isdir():
                continue
            require(member.isfile(), "NONREGULAR_SOURCE_FILE")
            if member.name.endswith(".py"):
                sources[member.name] = contents.extractfile(member).read()
    return commit, sources


def certified_revision(root: Path, expected: str, revisions=None) -> str:
    """Resolve the committed source identity accepted by the active certificate."""
    if revisions is None:
        revisions = subprocess.check_output(
            ["git", "-C", str(root), "rev-list", "--all", "--", "src/balatro_horizons"],
            text=True,
        ).splitlines()
    for revision in revisions:
        commit, sources = revision_sources(root, revision)
        if fingerprint_sources(sources) == expected:
            return commit
    raise ValueError("CERTIFIED_BASELINE_REVISION_NOT_FOUND")


def _read_certificate(root: Path) -> tuple[dict, str]:
    old = json.loads((root / "private/capability-certificate.json").read_text())
    require(old.get("status") == "passed", "NATIVE_EVIDENCE_NOT_PASSED")
    identifier = old.get("certificate_id", "")
    require(
        len(identifier) == 32 and all(char in "0123456789abcdef" for char in identifier),
        "INVALID_CERTIFICATE_ID",
    )
    original = json.loads((root / "private" / f"capability-record-{identifier}.json").read_text())
    require(original == old, "ACTIVE_CERTIFICATE_RECORD_MISMATCH")
    return old, identifier


def _resolve_baseline(root: Path, old: dict, baseline: str) -> str:
    if baseline == "auto":
        return certified_revision(
            root, old.get("accepted_implementation_hash", old.get("implementation_hash"))
        )
    return baseline


def _check_evidence(root: Path, old: dict, baseline_hash: str) -> dict:
    evidence = json.loads((root / "reports/verification/native-evidence.json").read_text())
    require(
        evidence.get("environment_hash") == old["environment_hash"],
        "NATIVE_EVIDENCE_ENVIRONMENT_MISMATCH",
    )
    require(
        evidence.get("accepted_implementation_hash", evidence.get("implementation_hash"))
        == baseline_hash,
        "NATIVE_EVIDENCE_SOURCE_MISMATCH",
    )
    require(
        all(
            (root / artifact).is_file()
            for entry in evidence.values()
            if isinstance(entry, dict) and entry.get("evidence_kind") == "NATIVE"
            for artifact in entry.get("artifacts", [])
        ),
        "NATIVE_ARTIFACT_MISSING",
    )
    return evidence


def _native_identity(old: dict, before: dict[str, bytes], candidate: dict[str, bytes]):
    baseline_manifest = native_component_manifest(before)
    candidate_manifest = native_component_manifest(candidate)
    require(candidate_manifest == baseline_manifest, "NATIVE_GAME_SOURCE_CHANGED")
    native_hash = native_implementation_fingerprint(before)
    require(
        old.get("native_implementation_hash", native_hash) == native_hash,
        "BASELINE_NATIVE_MANIFEST_MISMATCH",
    )
    require(
        old.get("native_component_manifest", baseline_manifest) == baseline_manifest,
        "BASELINE_NATIVE_MANIFEST_MISMATCH",
    )
    return baseline_manifest, native_hash


def prepare(root: Path, candidate: Path, baseline: str, offline_report: Path):
    """Build a reuse certificate without changing the live workbench."""
    root, candidate = native_path(root), native_path(candidate)
    old, identifier = _read_certificate(root)
    baseline = _resolve_baseline(root, old, baseline)
    commit, before = revision_sources(root, baseline)
    baseline_hash = fingerprint_sources(before)
    require(
        old.get("accepted_implementation_hash", old.get("implementation_hash")) == baseline_hash,
        "BASELINE_NOT_CERTIFIED",
    )
    after = source_files(candidate)
    before_native_manifest, native_hash = _native_identity(old, before, after)
    before_native = native_hash
    require(old["environment_hash"] == lock_digest(root), "NATIVE_ENVIRONMENT_CHANGED")
    evidence = _check_evidence(root, old, baseline_hash)
    source = fingerprint_sources(after)
    report = json.loads(native_path(offline_report).read_text())
    require(
        report.get("status") == "passed"
        and report.get("implementation_hash") == source
        and report.get("suite") == "check_offline",
        "MATCHING_OFFLINE_CHECKS_REQUIRED",
    )
    reuse = {
        "kind": "unchanged_native_components",
        "baseline_revision": commit,
        "parent_certificate_id": identifier,
        "parent_certificate_hash": digest(old),
        "offline_report_hash": digest(report),
        "native_game_fingerprint": before_native,
        "native_component_manifest": before_native_manifest,
        "native_launches": 0,
        "checkpoint_certificates_migrated": False,
    }
    certificate = {
        **old,
        "schema_version": 2,
        "certificate_id": uuid.uuid4().hex,
        "created_at": now(),
        "accepted_implementation_hash": source,
        "native_implementation_hash": native_hash,
        "native_component_manifest": before_native_manifest,
        "validation_kind": "harness_compatibility",
        "reuse": reuse,
        "native_validation_created_at": old.get("native_validation_created_at", old["created_at"]),
    }
    return certificate, {
        **evidence,
        "accepted_implementation_hash": source,
        "native_implementation_hash": native_hash,
        "native_component_manifest": before_native_manifest,
        "reuse": reuse,
    }


def activate(root: Path, candidate: Path, certificate: dict, evidence: dict):
    """Atomically publish a prepared receipt after candidate installation."""
    root, candidate = native_path(root), native_path(candidate)
    require(
        fingerprint_sources(source_files(root)) == certificate["accepted_implementation_hash"],
        "CANDIDATE_NOT_INSTALLED",
    )
    require(
        fingerprint_sources(source_files(candidate)) == certificate["accepted_implementation_hash"],
        "CANDIDATE_CHANGED",
    )
    active = json.loads((root / "private/capability-certificate.json").read_text())
    require(digest(active) == certificate["reuse"]["parent_certificate_hash"],
            "ACTIVE_CERTIFICATE_CHANGED")
    require(lock_digest(root) == certificate["environment_hash"], "NATIVE_ENVIRONMENT_CHANGED")
    record = root / "private" / ("capability-record-" + certificate["certificate_id"] + ".json")
    atomic_json(record, certificate, immutable=True)
    atomic_json(
        root / "reports/verification" / ("native-evidence-reuse-" + certificate["certificate_id"] + ".json"),
        evidence,
        immutable=True,
    )
    atomic_json(root / "reports/verification/native-evidence.json", evidence)
    atomic_json(root / "private/capability-certificate.json", certificate)
