# Retrospective decision summaries

## Browser explorer

Choose **Runs → Explore decisions** to open the full-run explorer. Use ante buttons,
search (including recorded notes), and action filters to find purchases, plays,
skips, ordering changes, or uncommitted requests. Selecting a choice shows its
recorded result and model note, with before/after boards and an annotation shortcut.
Previous/next follows the filtered list. On a phone, **Back to choices** returns to
the selected row. Browser URLs preserve the episode and selected decision on reload.

The explorer shows outcomes and records retrospective exposure. **Review →** still
opens staged prospective review. The server rejects index, detail, and jump requests
from prospective tokens. Explorer detail reads do not move the annotation cursor;
**Annotate this decision** explicitly positions it and saves retrospective provenance.
Existing annotations and journals are unchanged.

Browser numbering starts at 1; raw journal IDs and the existing annotation fields
remain zero-based. Ante grouping follows the pre-action observation. Native code
can advance the ante before boss cash-out, so that cash-out can appear in the next
ante. No new horizon rubric or automatic strategic assessment is introduced.

Recheck a recorded native run in the browser without game/provider calls:

```sh
node scripts/verify_browser_native.mjs --explore EPISODE_ID 0
```

Use the pinned Node version. The optional decision is a zero-based journal ID.
This checks desktop and phone browsing, records exposure, and writes screenshots
and a small verification result under `reports/verification/native-explorer*`.

## Saved reports

Generate an ante-grouped Markdown ledger and structured JSON from one recorded run:

```sh
uv --offline --directory /root/dev/balatro-horizons run ./scripts/summarize_run.py \
  --episode-id EPISODE_ID \
  --output reports/run-overviews/EPISODE_ID-decisions.json \
  --markdown-output reports/run-overviews/EPISODE_ID-decisions.md
```

Use fresh output filenames; existing files are never overwritten. The command contacts neither the game nor a model provider and requires no API credentials. It verifies the source journal hash chain and records full-run retrospective review exposure. The source journal itself is unchanged.

The report uses public observations and action envelopes. It includes plays and discards, purchases and pack selections, sales, skips, reorders, cash changes, visible Joker additions/removals, and recorded model notes. Uncommitted requests appear separately from completed actions. Notes remain claims by the model, even when they disagree with the actual transition.

The ante comes from the pre-action observation, so a boss cash-out can carry the next ante number. Scoring rows retain the pre-play target because the native observation can reset the target to zero after a clear. Card and offer names are the public labels available in the journal; pack offers may have generic labels such as `Default Base`. The JSON retains their public effects.

Provider prompts, raw/opaque provider outputs, helper-result bodies, agent context snapshots, and annotations are excluded from this report's input projection. The existing privacy scan still checks retained content against credential/path patterns and the episode's private seed. This does not alter the separate full-export API. Markdown table cells escape model-authored content.

For an at-a-glance narrative, summarize the generated ledger and link back to its decision IDs. Distinguish reported intentions from observed results, preserve uncertain failure causes, and avoid assigning optimality or causal credit from this descriptive evidence alone.
