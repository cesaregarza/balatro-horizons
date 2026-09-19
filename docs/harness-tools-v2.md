# Named game tools: harness revision 2

The first Luna trace exposed interface friction. All 54 responses were action
attempts; none used rules, history or arithmetic, and none updated memory. Two
rejected operations put a decision note at the wrong level of the nested schema.
Two others tried to select a revealed blind that was no longer the current blind.
Feedback said `UNKNOWN_BLIND` without identifying the valid choice.

These are observed protocol problems. They do not establish that the harness
caused the game loss, or that the model lacked a particular reasoning ability.
See [the recorded audit](../reports/verification/luna-harness-audit-v1.json).

## Implemented changes

- Named gameplay tools such as `play_hand`, `discard`, `buy`, and `select_blind`
  replace the nested `operate → kind → envelope → action` call in this revision.
  Only tools for the current phase are offered. Flat argument schemas include
  bounded selection counts, a current observation ID, and optional notes. The
  follow-up fix constrains ID parameters to current visible IDs, preventing the
  transcription error observed during the trial when strict schemas apply.
- `select_blind` and `skip_blind` explicitly identify the current blind. Their
  descriptions distinguish fighting from taking a skip reward. They do not rank
  strategic options or supply a recommended move.
- The automatic view retains current cards, effects, counters, resources, offers,
  hand levels, persistent effects, and all revealed blinds. It includes the latest
  two public events. `inspect_state` provides full public deck information and the
  latest 20 public events on demand. Earlier public history remains accessible
  through `read_history`.
- `read_rules`, `read_history`, `calculate`, and `inspect_state` are separate tools.
  They leave the native game unchanged. Helper calls and their results stay in the
  same decision loop, using provider tool-call/result messages. Inspected sections
  appear once in the observation; acknowledgments identify their paths. Older
  inspection exchanges are coalesced with explicit delivery metadata.
- Rejections explain affected fields or legal selections and confirm that the
  game did not advance. The original call remains recorded; no action is repaired
  or substituted by the harness.
- The browser exposes a game-interface selector and additional model-settings JSON.
  Previously the JSON settings value had no editing control.

The interface follows the [official function-calling guidance](https://developers.openai.com/api/docs/guides/function-calling)
on descriptive tools, schemas, and tool-result exchanges. OpenAI uses strict flat
schemas; Anthropic receives the same names, descriptions, and parameter schemas.
Its live compatibility remains untested.

## Context and provenance

This is an explicit information-presentation revision. It changes which public
details are automatically supplied and which require retrieval. The full masked
observation remains immutable in the journal. Exact request bodies, omitted event
IDs, helper returns, responses, errors, and requested actions are recorded.
`inspect_state` reads only the masked observation; it cannot access native saves,
seeds, engine internals, or future observations.

The existing explicit-memory policy remains: context is rebuilt after each game
action, with up to 4,096 characters of agent-authored memory. Within a decision,
retrieved current-state sections are retained once; other public helper exchanges
accumulate. Provider-owned reasoning state is not carried
forward, and returned reasoning summaries remain reports rather than access to
internal computation. Persistent opaque reasoning and an unrestricted accumulating
conversation would be separate protocol changes.

One game action executes at a time, followed by its settled native state. Read-only
helpers remain limited to eight per decision, and the original action, request,
invalid-action, and cost ceilings still apply. Coalescing preserves the full
values returned by inspection and records the replaced exchange indices. Other
large helper results can still hit the input ceiling; the harness fails explicitly
rather than silently dropping required information.

## Configuration and reproduction

Set `settings.harness_interface` to `tools_v2`. Omitting it retains `operate_v1` for
existing configurations. The new preset is `configs/luna-tools-smoke.yaml`;
`configs/luna-smoke.yaml` retains the original presentation. Both use the same
Luna model, reasoning settings, and Red/White plumbing configuration.

```bash
# Read-only reconstruction of candidate requests against the recorded native run.
uv run python scripts/audit_harness.py \
  --episode-id c0ed932043be429d972806833b4bb145 --compare-tools-v2

# Uses the existing campaign ledger and a separately frozen configuration revision.
uv run --env-file /path/to/openai.env \
  python scripts/smoke_openai.py --config configs/luna-tools-smoke.yaml \
  --revision tools-v2-retrieval --allow-paid
```

The initial trial used `tools-v2`; follow-up validation uses `tools-v2-retrieval`.
Neither replaces an earlier frozen configuration or resets the ledger. Exact
source fingerprints distinguish the implementation revisions. The continuing
smoke campaign retains its authorized $1 episode
and $5 total limits. Installation and this documentation authorize no additional
spending. All smoke episodes remain excluded from performance scores.

## Verification

For the initial named-tools build, reconstructing the first request at each of the original 50 native decisions
passed with no context-size failures. Median request size changed from **23,497.5
bytes to 14,889 bytes**, about **37% less**. These are complete JSON payload bytes,
not live token counts, and do not establish an improvement in playing strength.
See [the reconstruction audit](../reports/verification/luna-harness-audit-v2.json).

Mocked OpenAI and Anthropic runs pass inspection, arithmetic, rejected-action
recovery, memory updates, terminal recording, accounting, and progressive reveal.
Tests also cover concealed-state noninterference, unavailable-tool rejection,
explicit current-blind constraints, and retrieval without mutation.

The initial named-tools build passed **91 Python tests**, Ruff, the browser build, and both desktop/mobile browser
tests passed. Native seed replay and direct restoration were re-certified in
three fresh processes per proof, and an immutable branch completed with its
parent unchanged. `bh doctor` passes with the new source fingerprint; see the
[native verification record](verification.md#named-tools-harness-follow-up).

A recorded-public-context API probe completed successfully, with an
estimated cost **$0.0003632**. It proves basic API/schema compatibility only.
Native recertification and a full native run are tracked separately.

The native trial `3fff6e2f4f3c489aa6dc69d5214444b4` committed **98 actions** and
received **105 responses**, including **six inspection calls**. It used agent-authored
memory and returned 64 reasoning summaries. One malformed card ID was rejected
and corrected through feedback. Estimated usage cost was **$0.1181754**.

The run stopped in the **Ante 4 shop with `INFRASTRUCTURE_FAILURE` /
`REQUIRED_CONTEXT_EXCEEDS_LIMIT`**, not a native game loss. A large inspection
result duplicated information in automatic context and crossed the conservative
request-size ceiling. No provider request was sent for that final oversized
context. The original trace and failure outcome remain unchanged.

The subsequent fix delivers requested sections once, with references in tool
acknowledgments, and constrains public IDs in schemas. Reconstructing **all 106
contexts**, including the failed one, now passes. The largest reconstructed
request is **27,753 bytes**; the failed decision's request is **25,749 bytes**.
Both fit the existing ceiling including the 4,096-byte framing allowance. This is
deterministic request validation, not a successful native continuation or a model
performance comparison. See [the final audit](../reports/verification/luna-tools-final-audit.json)
and [sanitized trace](../reports/verification/public-luna-tools-3fff6e2f4f3c489aa6dc69d5214444b4.json).

A single-call real-API replay of decision 98 is prepared in
`scripts/probe_recorded_context.py`. It validates a proposed operation and executes
no game action. Its preflight excludes the private seed and passes the public
export scanner. Automatic approval review requires explicit user authorization
for that recorded-context replay; two rejected tool launches sent no API calls.
The corrected full native run and a live replay response remain unverified until
their respective checks complete.

Final verification after these fixes: **94 Python tests passed with no skips**,
Ruff passed, and native replay, direct restoration, and the immutable branch check
were repeated against source `b8cee4a3bd25f9953cf1f2c42de878fa789c78b031872fb764f12807675f20e7`.
`bh doctor` passes with no blockers. The workbench serves this source and includes
a separately configured `luna-tools` player; paid execution remains disabled in
the browser's saved settings.

The initial named-tools transport and native trial cost an estimated **$0.1185386**
combined. Total campaign accounting is **$0.316724 of $5**, including **$0.114688**
retained from earlier unknown-usage credit failures. These are usage estimates
and reservations, not provider invoices.
