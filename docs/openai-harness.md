# OpenAI harness and Luna smoke

The [named-tools revision](harness-tools-v2.md) adds phase-specific gameplay tools,
on-demand public inspection, and a smaller automatic context. This page records
the original `operate_v1` smoke and its results; its preset remains reproducible.

The existing shared runner uses the direct OpenAI Responses API. The Luna preset
is `configs/luna-smoke.yaml`: `gpt-5.6-luna`, medium reasoning effort, returned
reasoning summary requested with `auto`, Red Deck / White Stake, 32,768 input-token
ceiling and 8,192 output-token ceiling. Output usage includes reasoning tokens.
Gold remains the evaluation default in `configs/pilot.yaml`.

Standard input/output prices were checked on 2026-09-14: $0.20 / $1.20 per million
tokens. The request explicitly selects the standard service tier. Luna requests
use explicit caching mode without breakpoints, which disables cache reads and
writes. The harness rejects Luna input ceilings above 272,000 until long-context
pricing is separately configured. At the preset ceilings, each request reserves
$0.016384 before sending. Unknown usage retains that reservation, including failed
transport attempts; later requests stop before exceeding the remaining allowance.

Sources: [Luna model](https://developers.openai.com/api/docs/models/gpt-5.6-luna),
[pricing](https://developers.openai.com/api/docs/pricing),
[prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching),
[reasoning summaries](https://developers.openai.com/api/docs/guides/reasoning).

## Run a smoke

Supply `OPENAI_API_KEY` through the backend environment or a private environment
file on Linux. Keep that file owner-readable only. Credentials never enter run
configuration, manifests, journals, or browser settings.

```bash
# Replace /path/to/openai.env with your private environment file.
uv --directory /root/dev/balatro-horizons run --env-file /path/to/openai.env python scripts/smoke_openai.py --dry-run
uv --directory /root/dev/balatro-horizons run --env-file /path/to/openai.env python scripts/smoke_openai.py --allow-paid
```

For an initial real-API check without controlling the Windows game, add
`--transport-only --allow-paid`. That runs the same model runner against a
synthetic fixture with a one-action limit, clearly labels its evidence
`SYNTHETIC_TEST`, and uses the same shared campaign ledger. It verifies transport
and parsing only. The full native command remains separately gated.

Without `--allow-paid`, the command only checks readiness. Each paid invocation
runs one native episode through `RunService` and `Runner`, up to a terminal event
or an explicit failure/budget limit. It uses ordinary game mechanics and the
normal native certificate gate. The smoke is excluded from benchmark scoring.

The operator authorized $1 per episode and $5 total for this smoke campaign.
`private/openai-luna-smoke/spending.json` persists accounting across invocations;
rerunning the script does not reset the total. The first execution freezes the
campaign configuration. Do not delete or replace its configuration or ledger to
retry; configuration changes need a new recorded revision that retains prior
spending. The current command rejects configuration mismatches. A native failure before any provider request costs zero. Request
failures with unknown usage retain their conservative reservation.

Reports go to `reports/verification/luna-smoke-EPISODE_ID.json`. The ordinary run
library lists the episode for review, with immutable public/private traces in the
usual locations. The script does not make a full-run success claim for a partial
or budget-exhausted episode. Its return code is zero only for a verified native win
or game loss; transport-only mode instead requires exactly one committed fixture action.

## What is recorded

Every request records exact public context, helper exchanges, provider settings,
and its reservation. Every decoded response is journaled before parsing, including
returned summaries and token usage. Exactly one complete `operate` function call
is accepted; invalid operations receive the existing bounded feedback sequence.
No private game controls or provider-hosted search/tools are available.

The explicit context/memory policy remains unchanged: current public state, up to
20 recent public events, and up to 4,096 characters of agent-authored memory.
Provider-native reasoning is not carried forward as hidden extra memory. A summary
is a model-generated account, not access to raw internal reasoning. The browser
shows returned summaries only after the existing action-reveal boundary. A missing
summary does not establish that an option was absent from internal consideration.

Mocked HTTP tests cover a complete synthetic episode through helper calls, native-
style action validation, memory updates, exact journaling, prospective isolation,
and accounting. They also cover incomplete/failed responses, invalid usage, and
unknown-usage retries exhausting the cap. These tests are explicitly synthetic;
only separately recorded live requests establish API compatibility.

## Live check status (2026-09-14)

The funded check passed through the real Responses API and a complete native
Red/White run. Episode `c0ed932043be429d972806833b4bb145` ended in a verified
**game loss on Ante 2**, with **50 committed actions, 54 responses, and $0.0831404**
in estimated usage cost. All requests returned completed responses; there were no
provider transport errors. Invalid operations received feedback and Luna continued
without human action overrides. Returned reasoning summaries appeared in 41
responses. This diagnostic smoke is excluded from benchmark scores and does not
establish a win rate or long-horizon reasoning ability.

The preceding one-action synthetic transport check,
`8ee30f2842e7457bacf8893f6ead9d20`, cost $0.000357. Together, the funded checks
used an estimated **$0.0834974**. Seven earlier HTTP 429 attempts retain
**$0.114688** in unknown-usage reservations, bringing campaign accounting to
**$0.1981854 of the authorized $5**. Reservations are not confirmed charges, and
usage estimates are not provider invoices. The earlier diagnostic identified
`credit_balance_exhausted` / `insufficient_quota`; the adapter now records that
safe code without retrying exhausted credits. Raw upstream error text stays out
of public logs.

The final suite passed **82 Python tests with no skips** and **2 browser tests**.
Native seed replay, direct-save restoration, and the immutable-branch check were
re-certified against the final harness source. See `reports/verification/openai-harness.json`
for the current status and exact attempt accounting.

The native smoke's hash chain and public export passed verification. Every review
boundary was checked against the live trace: responses and summaries appear only
after action reveal, and consequences appear afterward. Monitoring and export
exposure are recorded, so this run is already exposed for subsequent expert review.
Branching at a decision in this new episode still requires its own certificate.
Anthropic live execution and headless equivalence remain untested.

A final normal, certificate-gated native heuristic run also completed with a
verified game loss (five committed actions, no provider calls, calibration hooks
disabled). This checks startup and terminal recording, not Luna playing strength.

## Monitor a smoke

```bash
uv --directory /root/dev/balatro-horizons run python scripts/smoke_openai.py --status
# Optionally select a specific run:
uv --directory /root/dev/balatro-horizons run python scripts/smoke_openai.py --status --episode-id EPISODE_ID
```

Status reads only that episode's public journal and records the viewing exposure.
It reports phase, ante, committed actions, provider calls, returned responses,
settled usage estimates, unresolved reservations, and any terminal result. It does
not dump cards, model text, seeds, or credentials. Status requires no API key and
makes no provider request.


## Terra exploratory run — 2026-09-15

The `terra-tools` player uses the explicit `gpt-5.6-terra` model, medium reasoning,
auto returned reasoning summaries, `tools_v3`, and the existing frozen guide.
Its first diagnostic run uses Red/White and the same private seed as Luna episode
`a722f50bf84a41ff8fe94df76a7f916c`. It receives no previous model decisions or
reasoning. This is an exploratory comparison, not a model-performance estimate.

The [official model page](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
supports Responses, tools, structured output and medium reasoning. The
[standard pricing table](https://developers.openai.com/api/docs/pricing), checked
on 2026-09-15, gives $2/M ordinary input, $0.20/M cached input, $2.50/M cache
writes, and $12/M output. The current adapter disables caching explicitly only
for Luna. To preserve the existing certified source while supporting Terra's
default caching behavior, this player's accounting rate is conservatively set
to **$2.50/M for all input tokens**, with $12/M output. Displayed costs can exceed
provider charges. The 32,768 input bound is below long-context pricing thresholds.

The run retains the operator's $1 episode and $5 total authorization. Before
launch, deduplicated prior reservations and settlements totaled $0.3220322,
leaving room for this single run's entire $1 allowance. The default browser
runner's spending ledger is episode-local; this preflight accounts for earlier
attempts across ledgers rather than treating a new episode as a reset of the
operator's total authorization. No larger campaign is authorized by this run.

Register another model reproducibly with explicit accounting rates:

```bash
uv --directory /root/dev/balatro-horizons run scripts/register_player.py \
  --alias terra-tools --clone luna-tools --model gpt-5.6-terra \
  --input-rate 2.5 --output-rate 12 --pricing-date 2026-09-15 --apply
```

Without `--apply`, this previews only. Existing differing aliases are rejected;
budgets, skills and other players are preserved. Registration makes no paid call.
Episode `5134a143d951415d976ca454e5e087c1` and its immutable
`reports/verification/terra-smoke-5134a143d951415d976ca454e5e087c1.json` record
the first Terra launch. Use the existing status command with its episode ID to
monitor it without revealing seeds or raw engine state.
