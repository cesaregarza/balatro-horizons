"""The access settings file and the preflight's bundle check."""

import hashlib
import json
from pathlib import Path

import pytest
from cloud_support import FINGERPRINT, FIREWALL_ID
from pydantic import ValidationError

from balatro_horizons.cloud.access import AccessError, AccessSettings, Bundle, load_access
from balatro_horizons.cloud.preflight import CHUNK_BYTES, bundle_matches

VALID = {
    "ssh_key_path": "/stand-in/access_key",
    "ssh_key_fingerprint": FINGERPRINT,
    "firewall_id": FIREWALL_ID,
    "bundle": {"path": "/stand-in/game-bundle.tar", "name": "game-bundle.tar",
               "bytes": 10, "sha256": "0" * 64},
}


def written(tmp_path, text):
    path = tmp_path / "cloud-access.yaml"
    path.write_text(text)
    return path


def test_a_valid_file_loads_as_frozen_settings(tmp_path):
    yaml_text = "\n".join([
        f"ssh_key_path: {VALID['ssh_key_path']}",
        f"ssh_key_fingerprint: {VALID['ssh_key_fingerprint']}",
        f"firewall_id: {VALID['firewall_id']}",
        "bundle:",
        *(f"  {name}: {value}" for name, value in VALID["bundle"].items()),
    ])
    # Unquoted, the digest of zeros and a fingerprint of decimal pairs read as numbers in
    # plain YAML; here they stay text.
    sexagesimal = "12:34:56:12:34:56:12:34:56:12:34:56:12:34:56:12"
    access = load_access(written(tmp_path, yaml_text.replace(VALID["ssh_key_fingerprint"],
                                                             sexagesimal)))
    assert access == AccessSettings.model_validate(VALID | {"ssh_key_fingerprint": sexagesimal})
    assert access.bundle.path == Path("/stand-in/game-bundle.tar")
    with pytest.raises(ValidationError):
        access.firewall_id = "another"


def test_a_missing_file_is_named(tmp_path):
    with pytest.raises(AccessError, match="CLOUD_ACCESS_MISSING"):
        load_access(tmp_path / "absent.yaml")


@pytest.mark.parametrize("change", [
    {"ssh_key_path": "relative/access_key"},
    {"ssh_key_fingerprint": "SHA256:not-the-provider-form"},
    {"firewall_id": "workers"},
    {"bundle": VALID["bundle"] | {"sha256": "F" * 64}},
    {"bundle": VALID["bundle"] | {"name": "../escape"}},
    {"bundle": VALID["bundle"] | {"bytes": 0}},
    {"region": "nyc3"},
])
def test_an_invalid_file_is_named(tmp_path, change):
    with pytest.raises(AccessError, match="CLOUD_ACCESS_INVALID"):
        load_access(written(tmp_path, json.dumps(VALID | change)))


@pytest.mark.parametrize("text", ["[unclosed", "just a string", ""])
def test_a_file_that_is_not_a_mapping_is_named(tmp_path, text):
    with pytest.raises(AccessError, match="CLOUD_ACCESS_INVALID"):
        load_access(written(tmp_path, text))


def test_an_unreadable_path_is_named(tmp_path):
    with pytest.raises(AccessError, match="CLOUD_ACCESS_INVALID"):
        load_access(tmp_path)


def bundle_for(path, data):
    return Bundle(path=path, name=path.name, bytes=len(data),
                  sha256=hashlib.sha256(data).hexdigest())


def test_a_bundle_larger_than_one_chunk_is_hashed_in_full(tmp_path):
    data = b"0123456789" * (CHUNK_BYTES // 4)
    path = tmp_path / "game-bundle.tar"
    path.write_bytes(data)
    assert bundle_matches(bundle_for(path, data))
    path.write_bytes(data[:-1] + b"!")
    assert not bundle_matches(bundle_for(path, data))


def test_the_size_is_checked_before_anything_is_read(tmp_path, monkeypatch):
    path = tmp_path / "game-bundle.tar"
    path.write_bytes(b"short")

    def unread(*args, **kwargs):
        raise AssertionError("a bundle of the wrong size must not be read")

    monkeypatch.setattr(Path, "open", unread)
    assert not bundle_matches(bundle_for(path, b"longer contents"))


def test_a_bundle_whose_file_name_or_presence_differs_never_matches(tmp_path):
    path = tmp_path / "game-bundle.tar"
    path.write_bytes(b"contents")
    renamed = bundle_for(path, b"contents").model_copy(update={"name": "other.tar"})
    assert not bundle_matches(renamed)
    path.unlink()
    assert not bundle_matches(bundle_for(path, b"contents"))
