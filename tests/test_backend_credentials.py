import importlib.util
import json
import stat
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "backend_credentials", Path(__file__).resolve().parents[1] / "scripts/configure_backend_credentials.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_import_is_private_persistent_and_does_not_echo_secret(tmp_path):
    root, units = tmp_path / "repo", tmp_path / "units"
    source = tmp_path / "temporary.env"
    source.write_text("OPENAI_API_KEY=mock-secret-not-a-key\n")
    preview = module.configure(root, units, source)
    assert not root.exists() and "mock-secret" not in json.dumps(preview)
    result = module.configure(root, units, source, apply=True)
    target = Path(result["environment_file"])
    assert target.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "mock-secret" not in json.dumps(result)
    assert Path(result["drop_in"]).read_text() == (
        f"[Service]\nEnvironmentFile=\nEnvironmentFile={target}\n"
    )
    source.unlink()
    module.configure(root, units, apply=True)
    assert target.read_text() == "OPENAI_API_KEY=mock-secret-not-a-key\n"


def test_empty_preparation_allows_unpaid_backend_without_changing_existing_keys(tmp_path):
    result = module.configure(tmp_path / "repo", tmp_path / "units", apply=True)
    assert result["credential_names"] == [] and result["provider_calls"] == 0
    assert Path(result["environment_file"]).exists()


@pytest.mark.parametrize("data", [b"PATH=/evil", b"OPENAI_API_KEY=", b"OPENAI_API_KEY='broken",
                                 b"OPENAI_API_KEY=one\nOPENAI_API_KEY=two", b"OPENAI_API_KEY=a b"])
def test_rejects_noncredential_or_malformed_input_without_echoing_it(data):
    with pytest.raises(ValueError) as error:
        module.credential_names(data)
    assert data.decode() not in str(error.value)


def test_refuses_symlink_destination(tmp_path):
    target = tmp_path / "providers.env"
    other = tmp_path / "untouched"
    other.write_text("unchanged")
    target.symlink_to(other)
    with pytest.raises(ValueError, match="SYMLINK"):
        module.atomic_private(target, b"new")
    assert other.read_text() == "unchanged"
