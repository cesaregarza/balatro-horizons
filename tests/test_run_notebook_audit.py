import importlib.util
import json

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.harness.context.memory import RunNotebook


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("audit_run_notebook", ROOT / "scripts/audit_run_notebook.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_delivery_detects_stale_notebook_and_distinguishes_attached_updates(module):
    notebook = RunNotebook()
    old = notebook.view()
    mutation, _ = notebook.propose("set_run_note", "plan", "Save cash")
    notebook.apply(mutation)
    current = notebook.view()
    values = [
        ("observation", {"observation_id": 0, "phase": "SHOP", "state": {}}),
        ("agent_operation", {"operation": {"kind": "action"}}),
        ("run_note", mutation),
        ("agent_context", {"context": {"run_notebook": current, "working_memory": {}}}),
        ("provider_request", {"body": {"input": [{"role": "user", "content": json.dumps({
            "observation": {}, "run_notebook": old})}]}}),
    ]
    events = [{"type": kind, "payload": payload, "sequence": i, "observation_id": 0,
               "hash": str(i)} for i, (kind, payload) in enumerate(values)]
    result = module.analyze(events, {"episode_id": "fixture"})
    assert result["updates"] == 1
    assert result["origins"] == {"attached_to_action": 1}
    assert result["mutations"][0]["decision"] == 1
    assert result["final_notebook"]["entries"] == {"plan": "Save cash"}
    assert [p["error"] for p in result["delivery_problems"]] == ["PROVIDER_NOTEBOOK_MISMATCH"]
    assert "| 1 |" in module.render([result])
    result["final_notebook"]["entries"]["plan"] = "<script>alert(1)</script>"
    assert "<script>" not in module.render([result])


def test_delivery_reads_text_blocks_without_confusing_later_helper_results(module):
    context = {"observation": {}, "run_notebook": {"entries": {"plan": "Save cash"}}}
    body = {"messages": [
        {"role": "system", "content": "Rules"},
        {"role": "user", "content": [{"type": "text", "text": json.dumps(context)}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "Ignored"}]},
    ]}
    assert module.delivered_context(body) == context
    assert module.delivered_context({"input": [{"role": "user", "content": "not JSON"}]}) == {}
