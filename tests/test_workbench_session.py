import pytest

from balatro_horizons.cli import workbench_session as module


def test_session_context_copies_only_launch_allowlist():
    source = {
        key: "/test/" + key
        for key in ("WSL_INTEROP", "SYSTEMROOT", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
    }
    source.update(
        OPENAI_API_KEY="secret", HTTP_PROXY="secret-proxy", WSLENV="OPENAI_API_KEY:HTTP_PROXY"
    )
    result = module.session_environment(source)
    assert "OPENAI_API_KEY" not in result and "HTTP_PROXY" not in result
    assert "secret" not in module.dropin(result)
    assert "APPDATA/p" in result["WSLENV"]
    with pytest.raises(ValueError, match="WINDOWS_CONNECTED"):
        module.session_environment({})


def test_dropin_preserves_paths_and_rejects_injected_directives():
    value = module.dropin({"APPDATA": '/test/user%name/"quoted"'})
    assert r'Environment="APPDATA=/test/user%%name/\"quoted\""' in value
    with pytest.raises(ValueError, match="INVALID_SESSION"):
        module.dropin({"APPDATA": "/test\nExecStart=unwanted"})
