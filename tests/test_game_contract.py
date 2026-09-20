"""All three RPC allowlists must stay in lockstep."""

import re

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.game.contract import (
    HARNESS_FAULT_CODES,
    INFRASTRUCTURE_CODES,
    NOT_ALLOWED_CODES,
    RPC_METHODS,
)


def _powershell_methods(source):
    match = re.search(r"\$allowed\s*=\s*@\(([^)]*)\)", source, re.DOTALL)
    assert match is not None
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def _lua_methods(source):
    match = re.search(r"local allowed\s*=\s*\{([^}]*)\}", source, re.DOTALL)
    assert match is not None
    return set(re.findall(r"([a-z_]+)\s*=\s*true", match.group(1)))


def _lua_error_codes(source, table):
    match = re.search(rf"local {table}\s*=\s*\{{([^}}]*)\}}", source, re.DOTALL)
    assert match is not None
    return set(re.findall(r"([A-Z][A-Z0-9_]*)\s*=\s*true", match.group(1)))


def _assert_allowlists_agree(powershell, lua, contract):
    assert powershell == lua == contract
    assert len(contract) == 15


def test_allowlists_agree():
    powershell = (ROOT / "native/bridge.ps1").read_text()
    lua = (ROOT / "native/patches/dispatch.lua").read_text()
    _assert_allowlists_agree(_powershell_methods(powershell), _lua_methods(lua), RPC_METHODS)


def test_lua_error_name_tables_agree_with_python_contract():
    lua = (ROOT / "native/patches/dispatch.lua").read_text()
    assert _lua_error_codes(lua, "action_codes") == NOT_ALLOWED_CODES
    assert _lua_error_codes(lua, "infrastructure_codes") == INFRASTRUCTURE_CODES
    assert _lua_error_codes(lua, "harness_codes") == HARNESS_FAULT_CODES


@pytest.mark.parametrize("missing_from", ["powershell", "lua", "contract"])
def test_allowlist_guard_rejects_a_missing_entry(missing_from):
    powershell = _powershell_methods((ROOT / "native/bridge.ps1").read_text())
    lua = _lua_methods((ROOT / "native/patches/dispatch.lua").read_text())
    contract = RPC_METHODS
    if missing_from == "powershell":
        powershell.remove("select")
    elif missing_from == "lua":
        lua.remove("select")
    else:
        contract = contract - {"select"}
    with pytest.raises(AssertionError):
        _assert_allowlists_agree(powershell, lua, contract)
