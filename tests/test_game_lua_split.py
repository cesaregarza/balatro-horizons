"""Focused lupa coverage for the issue-8 Lua manifest and settlement split."""

from lupa import LuaRuntime
from native_dispatch_harness import BOOTSTRAP

from balatro_horizons.config import ROOT

PATCHES = ROOT / "native/patches"


class SplitHarness:
    def __init__(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(BOOTSTRAP)
        sources = self.lua.table()
        for name in ("inspect.lua", "settle.lua", "action.lua", "dispatch.lua"):
            sources[name] = (PATCHES / name).read_text()
        self.lua.globals().module_sources = sources
        self.lua.execute(
            """
            local previous = SMODS.load_file
            SMODS.load_file = function(name)
              local source = module_sources[name]
              if source then
                local chunk, err = (loadstring or load)(source)
                assert(chunk, err)
                return chunk
              end
              return previous(name)
            end
            """
        )
        self.lua.execute((PATCHES / "horizons.lua").read_text())

    def request(self, method, params=None, request_id=1, token="secret-token"):
        payload = dict(params or {})
        payload["_bh_token"] = token
        request = self.lua.table_from(
            {"id": request_id, "method": method, "params": payload}, recursive=True
        )
        before = self.lua.eval("response_count()")
        self.lua.globals().BB_DISPATCHER.dispatch(request)
        if self.lua.eval("response_count()") == before:
            return None
        return self.lua.globals().responses[self.lua.eval("response_count()")]


def test_manifest_loads_split_modules_and_registers_private_endpoints():
    harness = SplitHarness()
    assert harness.lua.eval("registered.bh_action ~= nil")
    assert harness.lua.eval("registered.bh_inspect ~= nil")
    assert harness.lua.eval("type(love.update) == 'function'")


def test_action_and_harness_errors_use_distinct_taxonomy_names():
    harness = SplitHarness()
    harness.lua.execute(
        "BB_ERROR_NAMES.INFRASTRUCTURE='INFRASTRUCTURE_NAME'; "
        "BB_ERROR_NAMES.HARNESS_FAULT='HARNESS_FAULT_NAME'"
    )
    action_error = harness.request("bh_action", {"action": "teleport"})
    assert action_error.message == "UNKNOWN_ACTION"
    assert action_error.name == "NOT_ALLOWED_NAME"
    harness.lua.execute("G.STATE_COMPLETE=false")
    not_ready = harness.request("bh_action", {"action": "skip_pack"}, request_id=2)
    assert not_ready.message == "NOT_READY"
    assert not_ready.name == "INFRASTRUCTURE_NAME"
    harness.lua.execute("G.STATE_COMPLETE=true")
    harness_error = harness.request("private_debug", request_id=2)
    assert harness_error.message == "METHOD_FORBIDDEN"
    assert harness_error.name == "HARNESS_FAULT_NAME"

    harness.lua.globals().auto_respond = False
    harness.request("select", {"blind": "Small"}, request_id=3)
    infrastructure = harness.request("select", {"blind": "Big"}, request_id=4)
    assert infrastructure.message == "BUSY"
    assert infrastructure.name == "INFRASTRUCTURE_NAME"


def test_upstream_select_uses_the_same_thirty_plus_ten_settlement_rule():
    harness = SplitHarness()
    harness.lua.execute("next_response={accepted='select'}")
    assert harness.request("select", {"blind": "Small"}) is None
    harness.lua.globals().advance(39)
    assert harness.lua.eval("response_count()") == 0
    harness.lua.globals().advance(1)
    assert harness.lua.globals().responses[1].accepted == "select"


def test_checkpoint_guard_uses_the_active_runtime_not_a_fixed_drive():
    harness = SplitHarness()
    harness.lua.globals().auto_respond = False
    accepted = harness.request("save", {"path": "/isolated-runtime/checkpoints/abc123.jkr"})
    assert accepted is None
    assert harness.lua.eval("upstream_count()") == 1

    rejected = harness.request(
        "load", {"path": "/other-runtime/checkpoints/abc123.jkr"}, request_id=2
    )
    assert rejected.message == "PATH_FORBIDDEN"
    assert rejected.name == "HARNESS_FAULT_NAME"
