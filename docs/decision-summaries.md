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

In **Runs → Explore decisions**, use **Export JSONL** or **Export JSON**.
The browser downloads the existing server-projected, privacy-scanned ledger;
clicking Export makes no network request and does not control the game, update
settings, or move a review cursor. Opening the explorer retains its normal
retrospective exposure recording. The feature requires only a frontend update,
not a backend or game restart.

JSON has run metadata and a `decisions` array. JSONL has one object per represented
decision, with metadata repeated so each line can be interpreted independently.
Each decision contains separate `committed_actions` and `uncommitted_requests`
arrays; rejected or unresolved attempts at the same decision stay on the same
line. Raw decision IDs are preserved, including nonzero branch starting IDs.
The download includes all loaded ledger rows, regardless of active filters.

The version-1 `decision_summaries` format includes labels and selections, model
notes, observed scoring/resource/build changes, public run/model identity,
terminal summary when present, and the verified source journal head. It is a
decision-summary export, not a full observation/provider transcript. Full board
states, exact action envelopes, helper-only turns, provider prompts/outputs,
opaque continuations, memory, annotations and private state are omitted. These
limits are recorded in each export. Model-authored strings are JSON-escaped and
remain data when downloaded.
The journal head is a provenance reference, not a self-contained hash chain for
the transformed download.

An unfinished episode gets `snapshot_status: "in_progress"` and a `-partial`
filename; it cannot acquire a terminal outcome from a later refresh. Pending
transitions retain their recorded status. A completed episode gets `finished`.
JSON can also represent an empty decision ledger; JSONL is disabled until a
decision is available. Finished exports include the entire recorded decision
ledger; a running export contains the snapshot currently loaded in the browser.

Focused checks cover both downloaded formats, filter independence, partial
snapshots, multiple attempts per decision, private-field omission, escaped notes,
and absence of API mutations during export. Existing phone and live-update
checks use an isolated backend and synthetic fixtures, never the running game.
The production build and all six focused browser/export checks passed, as did
three frontend-publisher regression cases.

Frontend-only updates can be published without restarting an active worker:

```sh
.venv/bin/python scripts/deploy_frontend.py \
  --build /tmp/completed-vite-build \
  --dist /root/dev/balatro-horizons/web/dist \
  --backup /tmp/previous-workbench-index.html
```

Use a fresh backup filename. The helper checks referenced assets, rejects
conflicting existing assets, preserves old assets for open tabs, copies the
previous index, and replaces the served index atomically after copying the new
bundle. It never controls the service or game. This automates the recurring
frontend-only publish procedure.

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
