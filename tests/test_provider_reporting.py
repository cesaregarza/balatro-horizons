"""Synthetic display fixtures, not native or paid provider evidence."""

import json

import pytest

from balatro_horizons.review.decision_ledger import token_cost_accounting
from balatro_horizons.review.detail_projection import compact_detail, ordinary_events
from balatro_horizons.review.provider_projection import reported_usage, returned_reasoning
from balatro_horizons.review.summary_projection import event_projection


def response(body):
    return {"type": "provider_response", "event_id": "response", "payload": {"body": body}}


@pytest.mark.parametrize("body,expected", [
    ({"type": "message", "usage": {"input_tokens": 100, "cache_read_input_tokens": 1000,
                                    "cache_creation_input_tokens": 200, "output_tokens": 50}}, 1300),
    ({"output": [], "usage": {"input_tokens": 1300, "input_tokens_details": {"cached_tokens": 1000, "cache_write_tokens": 200},
                              "output_tokens": 50, "output_tokens_details": {"reasoning_tokens": 30}}}, 1300),
    ({"content": [], "usage": {"input_tokens": 100, "output_tokens": 50}}, 100),
])
def test_totals_agree_across_native_compact_ordinary_and_ledger(body, expected):
    event = response(body)
    original = json.dumps(event, sort_keys=True)
    projected = event_projection([event])
    ordinary = ordinary_events([event])
    compact = compact_detail({"action_events": [event]})["action_events"]
    for records in ([event], projected):
        totals = token_cost_accounting(records)
        assert totals["provider_input_tokens"] == expected
        assert totals["provider_output_tokens"] == 50
    for records in (ordinary, compact):
        usage = records[0]["payload"]["reported_usage"]
        assert usage["input_tokens"] == expected
        assert usage["output_tokens"] == 50
    assert ordinary[0]["payload"] == compact[0]["payload"]
    assert compact_detail({"action_events": ordinary})["action_events"] == compact
    assert json.dumps(event, sort_keys=True) == original


def test_openai_cache_subsets_are_reported_without_changing_total():
    assert reported_usage({"output": [], "usage": {"input_tokens": 1300, "output_tokens": 50,
        "input_tokens_details": {"cached_tokens": 1000, "cache_write_tokens": 200},
        "output_tokens_details": {"reasoning_tokens": 30}}}) == {
            "input_tokens": 1300, "output_tokens": 50, "cache_read_tokens": 1000,
            "cache_write_tokens": 200}


@pytest.mark.parametrize("value", [None, True, -1, 1.5, "123", [], {}])
def test_malformed_usage_is_unknown_not_zero_or_coerced(value):
    body = {"content": [], "usage": {"input_tokens": 100,
            "cache_read_input_tokens": value, "cache_creation_input_tokens": 200,
            "output_tokens": value}}
    assert reported_usage(body)["input_tokens"] is None
    assert reported_usage(body)["output_tokens"] is None
    for events in ([response(body)], event_projection([response(body)])):
        assert token_cost_accounting(events)["provider_input_tokens"] is None
        assert token_cost_accounting(events)["provider_output_tokens"] is None


def test_missing_usage_and_missing_input_are_unknown_and_no_calls_are_zero():
    for body in ({}, {"usage": None}, {"usage": {"cache_read_input_tokens": 100}}):
        assert reported_usage(body)["input_tokens"] is None
        assert reported_usage(body)["output_tokens"] is None
    assert token_cost_accounting([])["provider_input_tokens"] == 0
    assert token_cost_accounting([response({"usage": {"input_tokens": 10}}), response({})])[
        "provider_input_tokens"] is None


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-sonnet-4-6", "claude-opus-4-20250514"])
def test_claude_summaries_never_project_opaque_fields(model):
    body = {"model": model, "content": [
        {"type": "thinking", "thinking": "Save interest.", "signature": "OPAQUE_SIGNATURE"},
        {"type": "redacted_thinking", "data": "OPAQUE_DATA", "thinking": "OPAQUE_TEXT"},
        {"type": "text", "text": "UNRELATED_TEXT"},
    ]}
    expected = {"texts": ["Save interest."], "status": "returned", "redacted": True}
    assert returned_reasoning(body) == expected
    event = response(body)
    for projected in (ordinary_events([event]), compact_detail({"action_events": [event]})):
        rendered = json.dumps(projected)
        assert "Save interest." in rendered
        assert "OPAQUE" not in rendered and "UNRELATED" not in rendered
    assert "OPAQUE_SIGNATURE" in json.dumps(event)


@pytest.mark.parametrize("body,status", [
    ({}, "absent"),
    ({"model": "claude-haiku-5-5", "content": [{"type": "thinking", "thinking": ""}]}, "empty"),
    ({"model": "claude-haiku-5-5", "content": [{"type": "thinking", "thinking": "  "}]}, "empty"),
    ({"content": [{"type": "redacted_thinking", "data": "OPAQUE"}]}, "redacted"),
    ({"model": "claude-3-7-sonnet-20250219", "content": [{"type": "thinking", "thinking": "LEGACY"}]}, "unsupported"),
    ({"content": [{"type": "thinking", "thinking": "UNKNOWN"}]}, "unsupported"),
    ({"output": [{"type": "reasoning", "summary": [], "encrypted_content": "OPAQUE"}]}, "empty"),
])
def test_summary_absence_labels_are_honest(body, status):
    result = returned_reasoning(body)
    assert result["status"] == status
    assert result["texts"] == []
    assert "OPAQUE" not in json.dumps(result)
    assert "LEGACY" not in json.dumps(result) and "UNKNOWN" not in json.dumps(result)


def test_openai_summary_allowlist():
    result = returned_reasoning({"output": [{"type": "reasoning", "encrypted_content": "OPAQUE",
        "summary": [{"type": "summary_text", "text": "Save interest.", "signature": "OPAQUE"},
                    {"type": "unknown", "text": "OPAQUE"}, {"type": "summary_text", "text": 5}]}]})
    assert result == {"texts": ["Save interest."], "status": "returned", "redacted": False}
