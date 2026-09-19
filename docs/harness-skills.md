# Balatro skills in the playing harness

New runs have access to the [canonical Balatro guide](balatro-guide/README.md).
The harness already supports frozen rules lookup and a loop of read-only helpers
before each game action; skills use that loop. No filesystem, shell, network or
external-agent access is given to the playing model.

## Using the guide

In **Models & budgets → Game knowledge**, select **Balatro guide — rules and
strategy** and save. This is the default. Select **Native rules only** for an
explicit comparison without strategy skills. Existing episodes retain their
recorded information; changing this setting affects new runs.

The equivalent configuration is:

```yaml
skills: balatro-guide-v1 # or none
```

This fragment is not a complete paid-run configuration. Enabling skills does not
enable paid execution or change spending ceilings.

The first request at each game decision includes twelve skill names and short
descriptions, not their full text. Both providers receive the same
`read_skill(name)` tool and catalog. A model can read a relevant skill, follow its
chapter keys with `read_rules(key)`, inspect public state, and then choose one
legal game action.

Each read consumes one of the existing eight helper calls per decision and leaves
the game unchanged. Pages contain at most 4,096 UTF-8 bytes; `next_key` identifies
the next page and `complete` indicates whether the entry is finished. The shared
paging policy reduces page size when needed to fit the configured context bound.
If another page cannot fit, the helper returns `REFERENCE_CONTEXT_LIMIT` and
retains the existing context. It never silently removes earlier guide text.

Full descriptions are omitted after the first request in that decision; the tool
schema retains the available names. Retrieved text remains in the decision's
exchanges. After a game action, only the existing bounded, agent-authored memory
carries forward. A model may save a short rule reminder or chapter key there.
There is no automatic persistent skill cache.

## Frozen knowledge and branches

At episode creation, the harness checks the generated bundle hash and combines
its allowlisted entries with the environment-bound native rules dictionary. It
writes one immutable private `knowledge.json`; the public start event records
content and bundle hashes, and each checkpoint references that exact snapshot.
Requested public text, continuation offsets, exact requests, helper results and
context-delivery metadata remain in the normal journal and prospective review.

A branch verifies and inherits its parent's frozen knowledge even if the installed
guide or current settings change. Older checkpoints without a knowledge snapshot
cannot branch under this revision. A native restoration certificate and an intact
knowledge snapshot are both required. Native replay certification alone does not
invent the missing historical knowledge.

To revise the guide, edit its Markdown and regenerate its bundle:

```bash
uv run python scripts/package_balatro_guide.py \
  --guide docs/balatro-guide --output docs/balatro-guide.zip \
  --report reports/verification/balatro-guide.json
```

New episodes load the regenerated bundle; existing episodes remain frozen. A
missing or inconsistent bundle prevents a new guide-enabled run rather than
silently reverting to a different information condition.

## Verification

Mocked OpenAI and Anthropic runs exercise discovery, skill reads, reference reads,
actions, memory, journaling, and prospective reveal through the real harness.
Tests cover paging, invalid keys, helper limits, frozen branches, settings
persistence and the rules-only setting. These tests make no external API calls.

The [offline context audit](../reports/verification/skills-context-audit.json)
reconstructs the previous Luna trace with skill access and probes additional
chapter reads. This checks request construction, not playing strength or whether
a model will choose to consult the guide.

Unpaid native checks use `scripts/native_runs.py --read-skills`: each ordinary
Red/White and Red/Gold calibration run uses rules operations to request a skill
and reference before the
unchanged heuristic baseline plays. `scripts/verify_release.py
--resume-certification` then repeats native restoration and branching checks.
Their recorded results are separate from mocked-provider and request-size tests.
Live paid-provider use of the skill tool remains unverified until an explicitly
budgeted model run exercises it.

Current delivery: 113 Python tests pass with no skips. The three browser
checks, Ruff and browser build pass. Native runs retrieved skills, seed replay and
direct restoration passed three fresh-process repetitions, and a completed branch
preserved its parent's journal and frozen guide. Normal startup passes for both
Red/White and Red/Gold. The current certificate is active; live paid-provider use
of the skill tool remains unverified.
