# Focused context: tools_v3

`tools_v3` gives the playing model a compact current board and bounded, on-demand
access to the rest of its public information. It is a versioned information
presentation change. `tools_v2` and `operate_v1` remain available; existing episode
manifests and traces are never migrated.

In **Models & budgets → Game interface**, select **Focused context with paged
lookups** and save a player. It is the default for the new-player form. Existing
players keep their saved interface until explicitly updated. The CLI preset is
`configs/luna-focused-smoke.yaml`; it retains Red/White, the explicit Luna model,
medium reasoning, the $1 episode / $5 campaign ceilings, and disabled paid calls.

## Automatic information

The model receives its objective, explicit notes, current cards and order,
Jokers, consumables, shop or pack offers, resources, action constraints, persistent
effects and revealed blind effects. Active effects and nondefault counters remain
visible. Common card and counter defaults are declared once instead of repeated
for each card. Counter defaults apply only where `counter_defaults_apply` records
that all defaulted fields were actually observed; missing or concealed counters
remain unknown. Unrecognized counters, including zero-valued counters, are retained.
Concealed-card flags remain intact. No move ranking or tactical recommendation is
added by the presentation layer.

Hand levels are supplied while selecting a hand and retrieved elsewhere. Exact
public deck composition remains on demand. Two recent event previews are provided;
text over 240 characters is explicitly marked truncated and remains available via
inspection. The frozen skill catalog shows names and short description previews
on the first request of a decision. Full skill text is loaded only when requested.

## Retrieval and memory

- `inspect_state(section, offset)` returns one allowlisted current-public section
  as JSON text, in pages of at most 2,048 UTF-8 bytes. Start at offset zero and
  follow `next_offset`; a partial page is explicitly incomplete.
- `read_history(offset, limit)` returns compact event records with stable offsets.
  A page is bounded even if one recorded event is large.
- `read_history_detail(offset, byte_offset)` loads a complete past-public event
  through the same byte-paging mechanism. Even a supplied completed journal is
  cut off at the model's current observation, before pagination or lookup.
- `read_rules(key)` pages native rules and indexes, and follows frozen guide keys.
  `read_skill(name)` reads the requested frozen skill. `next_key` continues a rule
  or guide entry. Guide delivery in this mode uses 2,048-byte pages.
- `calculate` and the phase-specific gameplay tools retain their prior behavior.

The working context keeps the most recent three helper/error exchanges, or fewer
when required by its configured bound. `retrieval_context` explicitly lists the
cleared exchange indices and reload operations. Full original results remain in
the append-only journal; pruning changes only the next request. Nothing is
silently described as fully loaded after being cleared. Repeated reads are not
cached across game actions.

Up to 4,096 characters of agent-authored memory carry forward after a game action.
The model can preserve facts, plans and reference keys there. No secondary model
summarizes or judges its reasoning. Helper counts, provider/action limits and
spending ceilings are unchanged; the focused view also displays the remaining
helper allowance. An unreadable core board still fails explicitly instead of
inventing a substitute action.

## Request budgets and provenance

Budget planning uses the same message/tool serializer as real provider requests,
measuring the larger OpenAI/Anthropic representation. It includes JSON escaping,
4,096 bytes of framing allowance, and 1,024 bytes for model/settings fields.
The provider separately checks its complete configured body before reserving cost
or sending. These are conservative byte bounds, not measured tokenizer counts.
Provider-managed truncation stays disabled for OpenAI.

Both providers get the same public view, tools, guide, retention policy and memory
policy. Their native tool-call/result formats differ. Full canonical observations,
raw helper results, exact delivered context, retention metadata, provider bodies,
responses and action outcomes remain recorded for review. Frozen guide inheritance
and prospective-review rules are unchanged.

## Evidence

The saved 98-action Luna run was reconstructed without calling a model or the game.
Both candidate modes used the same current skill library:

| Check | Result |
|---|---|
| First requests at 99 observations | All constructed within bounds |
| Median first-request size, tools_v2 with guide | 18,186 bytes |
| Median first-request size, tools_v3 with guide | 16,345 bytes (10.1% smaller) |
| All 106 recorded contexts, including the original failed context | No size failures |
| Largest tools_v3 reconstructed request | 26,616 bytes |
| 2,544 additional guide-read reconstructions | No size failures |
| Largest guide-read reconstruction | 27,858 bytes |

The old live run predated guide integration and had a smaller 15,355-byte median
first request. It is not the like-for-like comparison above. Reconstructing legacy
helper payloads can clear them under the new policy; the audit records those
clearances. These checks establish request construction and bounds, not a model
performance improvement, a successful continuation, or live API acceptance.

Artifacts: `reports/verification/focused-context-audit.json` and
`reports/verification/focused-context-v2-comparison.json`. Reproduce with:

```bash
uv --directory /root/dev/balatro-horizons run python scripts/audit_harness.py \
  --episode-id 3fff6e2f4f3c489aa6dc69d5214444b4 \
  --candidate-interface tools_v3 --with-skills --check-contexts --probe-guide-reads
```

Offline tests cover both providers through the real runner: skill reads, inspection,
actions, invalid-action feedback, explicit memory, accounting and prospective
review. Additional tests check lossless UTF-8 paging, temporal history cutoffs,
read-only helpers, concealed-information noninterference, schema parity, source
immutability and bounded working-context receipts.

Activation completed on **2026-09-15**: both saved players, `luna` and
`luna-tools`, now use `tools_v3`. **124 Python tests pass with no skips**, including
the refreshed native evidence gates. Ruff, the Node 22.12.0 browser build, and all
three browser tests passed. Live desktop and mobile checks also passed against the
native review and branch comparison, including the actual Tailscale HTTPS route.

The action fixture and a fresh Gold calibration run each passed three
fresh-process seed replays with no divergence. Direct restoration at Gold
decision 0 passed three repetitions, and a native branch completed with its parent
unchanged. Ordinary Gold startup passed with calibration hooks disabled; doctor
reports no blockers for Red/White or Red/Gold. These native checks use the unchanged
heuristic baseline and establish pipeline fidelity, not live model performance.

See [activation evidence](../reports/verification/focused-activation.json),
[native replay and branch evidence](../reports/verification/native-release.json),
and [verification scope](verification.md). No paid calls were made during this
update; spending limits are unchanged and paid execution remains disabled.
Live paid-provider validation of `tools_v3` remains unverified.

## Design references

The implementation applies the bounded-history and observable-memory tradeoffs in
[OpenAI's session-memory guidance](https://developers.openai.com/cookbook/examples/agents_sdk/session_memory)
and the on-demand retrieval and tool-result-clearing patterns in
[Anthropic's context-engineering guidance](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).
Deterministic retrieval and explicit agent notes preserve inspectability for this
benchmark; provider-specific opaque compaction would be a different protocol.
