# Balatro Horizons

Balatro Horizons is a local benchmark for studying how agents balance survival
in the current blind, preparation for the ante, and development across a full
Balatro run. Models play complete native games; researchers can review decisions,
annotate tradeoffs, and test certified alternative continuations. Red Deck /
Gold Stake is the evaluation default; Red / White is the plumbing configuration.

## What this benchmark studies

The question is when an agent applies relevant knowledge and trades present
resources against future needs. Immediate means this blind, short means this
ante, and long means the full run. Annotation labels remain versioned until a
rubric migration is selected. The relationship to scheming is a hypothesis, not
an interpretation validated by a Balatro score.

The [dashboard and optional workbench](docs/dashboard.md) support review before
reveal, expert diagnosis with append-only annotations, and testable alternative
continuations under a matching certificate. `bh summarize` produces a public
decision ledger and optional Markdown recap, recording retrospective exposure.

| Project | Documented focus | Balatro Horizons emphasis |
| --- | --- | --- |
| [BALROG](https://arxiv.org/abs/2411.13543) | Long-horizon agents across game environments. | Blind/ante/run tradeoffs in one native game with prospective review. |
| [Evalatro](https://github.com/alesha-pro/evalatro) | Native runs, replays, and a public leaderboard. | Wins plus coverage and failure accounting with expert annotations. |
| [BalatroBench](https://github.com/coder/balatrobench) | BalatroLLM artifacts and strategy analysis. | Decision evidence, exposure control, and certified continuations. |

These comparisons were checked 2026-09-16 and are not an exhaustive novelty
review. See the [research reconciliation](docs/research/reconciliation-2026-09-14.md)
for accepted direction and unselected proposals.

## Architecture and local setup

The [architecture](ARCHITECTURE.md) describes the game, harness, and dashboard
layers. The [game interface](docs/game-interface.md), [harness](docs/harness.md),
[dashboard](docs/dashboard.md), and [evidence](docs/evidence.md) guides are the
living operational contracts. Historical decisions are in [CHANGELOG.md](CHANGELOG.md).
Use Python 3.12, uv 0.8.17, and the Node version pinned in `.nvmrc`.

```bash
git clone https://github.com/cesaregarza/balatro-horizons.git
cd balatro-horizons
uv sync --locked
npm --prefix web ci
npm --prefix web run build
uv run bh review --port 8765
```

Open `http://127.0.0.1:8765`. The default dashboard keeps run exploration,
settings, batches, reports, and retrospective annotations available. Staged
reveal, branches, comparison, human takeover, and verification require
`bh review --workbench`. Budget continuation remains deferred. The server binds
to loopback; a trusted Tailscale Serve proxy can provide remote access.

Synthetic episodes are application tests and are never native results. Native
execution requires the operator's licensed installation, matching source and
environment fingerprints, and the evidence gates documented in the evidence
guide. Credentials, seeds, raw engine state, saves, private manifests, and
journals stay outside public exports.

## Checks and evidence

Install the locked Python and web dependencies before running the offline gate:

```bash
uv run bh offline
```

The gate runs the Python checks without launching Balatro or making paid
provider calls. CI adds a separate web build and browser-test lane. Inspect the
native plan before any operator-authorized run:

```bash
uv run bh evidence plan
uv run bh evidence plan --gameplay-only
```

`evidence collect`, `evidence certify`, and `evidence publish` require the
operator's licensed native environment and are never fresh-clone setup steps.

A certificate covers only its pinned runtime, source identity, environment,
deck, stake, and recorded restoration scope. A successful save call alone does
not establish checkpoint fidelity. Failed or missing evidence is retained as
unresolved and is never silently converted into a pass.

## Operational commands

Use `uv run bh COMMAND --help` for flags and required inputs. These commands
retain the former scripts' defaults, guards, and output formats:

| Command | Purpose |
| --- | --- |
| `offline [--web] [--report PATH]` | Python checks, optional web lane, source-bound receipt |
| `deploy frontend` / `deploy candidate` | Frontend publication / idle candidate installation and rollback |
| `guide package` / `prompt sync` | Guide validation and packaging / persistent prompt freshness |
| `human register` | Preview or register a saved player's settings; does not start a run |
| `smoke` | Capped provider smoke; paid execution requires `--allow-paid` |
| `native diagnose` | Authorized native startup diagnostics |
| `review session` / `review status` | Preview/apply launch context / bounded worker status |
| `credentials` | Preview/apply owner-only backend provider credentials without printing values or private paths |

`scripts/` keeps only native bootstrap and the two-line offline CI entry point.
Browser-native verification lives at `web/scripts/verify_browser_native.mjs`;
its help and invalid-argument checks run in the web unit-test lane. Live browser
verification still requires the operator's existing runtime and recorded runs.

## Repository boundaries

`src/` contains the runtime and typed contracts; `web/` contains the operator
client; `docs/balatro-guide/` is the canonical rules reference. Public exports
are schema-selected and privacy-scanned. Private run data and generated reports
are local artifacts, not source-controlled inputs. See [third-party notices](docs/THIRD_PARTY.md)
and the [original contract](docs/contract/balatro_horizons_spec/BALATRO_HORIZONS_SPEC.md).

Money admission, reserve-before-send ordering, unknown-usage retention, and
durable campaign stops are documented in the [harness Money guide](docs/harness.md#money).
