# Provider continuation protocol v1

This is handoff work package B2 on branch `fix/provider-continuations`, based on
public-information commit `5361c4d`. It adds the versioned `tools_v5` harness.
The running workbench and dedicated Windows runtime were not updated. No paid
provider request or native game launch was performed.

## Decision boundary

`tools_v5` treats all helper calls before one game action as one decision. The
harness retains provider-native assistant output during that decision, including
opaque state required by the provider, and returns each helper result against the
provider's original call ID. A committed game action ends the decision. The next
observation rebuilds explicit context and carries only the benchmark's public
history and bounded agent-authored memory; provider-native continuation state is
not carried across that boundary.

This reset is a benchmark protocol choice, not a provider limitation. Existing
`operate_v1` and `tools_v2` through `tools_v4` retain their old request and generic
failure semantics. Saved model configurations are not rewritten. New model
defaults can select `tools_v5` in the browser or `register_player.py`.

## OpenAI Responses

OpenAI requests remain stateless with `store: false`. `tools_v5` requests
`reasoning.encrypted_content`, retains the returned response output items without
editing them, and sends those items back before matching `function_call_output`
items. It does not use `previous_response_id`. The API reference says reasoning
items must be included when manually managing subsequent input and that encrypted
reasoning enables stateless multi-turn use:

- [Create a model response](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
- [Responses streaming item schema](https://platform.openai.com/docs/api-reference/responses-streaming/response/refusal?lang=python)

The fixed tool catalog remains cacheable. For matched generation constraints,
`tools_v5` gives both providers the full catalog with automatic tool choice,
disables parallel tool use, uses strict schemas, and rejects phase-inactive game
tools locally. `tools_v4` retains OpenAI's API-side `allowed_tools` behavior for
historical compatibility. This distinction is part of the interface version.

## Anthropic Messages

The harness returns the complete assistant content array exactly as received,
including `thinking`, `redacted_thinking`, text, and `tool_use` blocks, followed
immediately by matching `tool_result` blocks. Error results set `is_error: true`.
This follows Anthropic's requirements that tool results immediately follow the
assistant tool-use message and that thinking blocks remain complete and unmodified:

- [Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)
- [Thinking with tools and preserved blocks](https://platform.claude.com/docs/en/build-with-claude/thinking)
- [Stop reasons and fallback](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)

The existing explicit `thinking_budget` setting remains the only configured
Anthropic thinking mode. This change does not infer adaptive-thinking support for
an unspecified model. Automatic tool choice works with the existing manual mode;
`tools_v5` does not use provider-specific forced tool selection.

## Failure feedback

New `tools_v5` traces distinguish:

| Condition | Feedback code |
|---|---|
| Completed response without a tool call | `NO_OPERATION` |
| More than one tool call | `MULTIPLE_OPERATIONS` |
| Phase-inactive or unavailable tool | `UNAVAILABLE_TOOL` |
| Invalid JSON arguments | `INVALID_OPERATION_JSON` |
| Non-object or schema-invalid arguments | Specific decoder/schema code |
| OpenAI incomplete response or Anthropic `max_tokens` | `PROVIDER_RESPONSE_INCOMPLETE` |
| Provider context ceiling | `PROVIDER_CONTEXT_LIMIT` |
| Provider refusal | `PROVIDER_REFUSAL` |
| Failed, unresolved, paused, or unexpected status | Corresponding provider-response code |

Multiple calls receive an error result for every call ID so no call remains
unresolved. Unavailable and malformed single calls receive a linked error result.
Truncated or otherwise unsafe partial turns are not replayed as complete tool
turns. Feedback contains only bounded status metadata, never upstream error text.
The existing three-consecutive-invalid-action limit remains unchanged.

## Context and exports

The latest three helper/error results remain loaded. When an older result is
cleared, `tools_v5` retains the provider protocol shell and substitutes a small
reload receipt for the result. If the required opaque continuation itself cannot
fit the declared context limit, the request fails rather than silently dropping
provider state.

Raw journals retain exact requests and responses for local audit. Public exports
recursively remove OpenAI `encrypted_content`, Anthropic thinking signatures, and
redacted-thinking data while retaining visible summaries/text plus an
`opaque_continuation_omitted` marker. Opaque state is never executed or exposed to
the gameplay adapter.

OpenAI explicit-cache pricing and worst-case reservation rules are unchanged.
Anthropic cache controls were not added: when Anthropic reports cache creation
without configured write pricing, settlement still conservatively retains the
full reservation.

## Verification limits

Offline tests cover exact request round trips for both providers, reasoning and
redacted-thinking blocks, decision-boundary reset, context clearing, multiple and
inactive calls, malformed JSON, stop-reason classification, conservative cache
accounting, legacy `tools_v4` behavior, and sanitized public export. Browser tests
cover selection and persistence of the new harness.

These tests use mocked HTTP transports and synthetic game mechanics. They do not
establish live model compatibility, provider billing behavior, gameplay quality,
or native runtime fidelity. A paid smoke probe needs explicit cost authorization.
Native deployment also requires the normal separately authorized release gates.
