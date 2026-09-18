"""Startup evidence may not silently accept changes to gameplay or restoration."""

import importlib.util
from copy import deepcopy

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.engine.provenance import source_files


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


scope = script("startup_scope")
reuse = script("reuse_native_evidence")


def test_actual_startup_patch_fits_narrow_boundary():
    _, before = reuse.revision_sources(ROOT, "HEAD")
    scope.require_startup_scope(before, source_files(ROOT))


@pytest.mark.parametrize("path,old,new", [
    ("engine/native.py", b'"RPC_RESPONSE_ID_MISMATCH"', b'"IGNORED_RESPONSE_ID"'),
    ("engine/native.py", b'process.stdin.write(line.encode("utf8"))', b'process.stdin.write(line.encode("ascii"))'),
    ("engine/native.py", b'len(result) > 16_000_000', b'len(result) > 32_000_000'),
    ("engine/replay.py", b'import ', b'import '),
])
def test_startup_scope_rejects_gameplay_changes(path, old, new):
    before = source_files(ROOT)
    after = deepcopy(before)
    key = "src/balatro_horizons/" + path
    assert old in after[key]
    after[key] = after[key].replace(old, new, 1)
    if old == new:
        after[key] += b"\n# changed replay\n"
    with pytest.raises(ValueError, match="CHANGED"):
        scope.require_startup_scope(before, after)
