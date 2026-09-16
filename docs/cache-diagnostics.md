# OpenAI cache diagnostics

The OpenAI adapter requests prompt-cache diagnostics for recognized GPT-5.6 and later model identifiers. Each request after the first completed response uses that response's ID in `prompt_cache_options.comparison_response_id`. A new episode or branch creates a fresh adapter with no comparison baseline. Responses without an explicit completed status and a nonempty string ID do not replace the last completed baseline.

This metadata does not load previous conversation content or change caching behavior. The harness still constructs its explicit public context for every decision. Luna retains its existing explicit caching mode; this change does not enable caching for Luna or alter pricing, spending limits, tool definitions, or game observations. Unsupported OpenAI models and Anthropic keep their existing requests.

Exact provider requests and responses remain in the append-only episode journal. Read `prompt_cache_diagnostics.type` separately from token usage:

- `cache_hit`: no miss was identified for the comparison; the entire request may still not be cached.
- `cache_miss`: inspect `reason`, `cache_missed_tokens`, and optional `comparison_reusable_tokens`.
- `comparison_response_not_found`: the baseline metadata may be missing or expired.
- `unavailable`, or an absent diagnostic result: no conclusive diagnosis was returned.

Actual cache reads and writes come from `usage.input_tokens_details.cached_tokens` and `cache_write_tokens`. Diagnostics report the first classified miss reason and are best effort. Their absence does not make an otherwise successful operation fail. Historical runs without diagnostics cannot establish why caching missed.

For a retrospective report, use a fresh output filename:

```sh
uv --offline --directory /root/dev/balatro-horizons run python scripts/audit_transcript_efficiency.py \
  --root /root/dev/balatro-horizons/data \
  --episode-id EPISODE_ID \
  --output /root/dev/balatro-horizons/reports/verification/cache-diagnostics-EPISODE_ID.json \
  --record-exposure
```

The schema-version-2 report includes an allowlisted diagnostic object for each request and counts by diagnostic type for each episode, with `missing` distinct from `cache_miss`. Aggregate usage reports `cached_input_tokens` and `cache_write_input_tokens`. The command records retrospective review exposure and does not make provider or game calls. It refuses to overwrite an existing report.

Diagnostics have no additional charge. Generation requests, including any extra probes, still incur normal provider charges and require the configured execution authorization and spending limits.

Changing the provider source invalidates the native implementation certificate. Complete the repository's native recertification process before using the updated source for native runs. Offline mocked tests validate request construction and reporting; they do not establish live cache behavior or native replay fidelity.

Source: [OpenAI prompt-cache diagnostics](https://developers.openai.com/api/docs/guides/prompt-caching/diagnostics).
