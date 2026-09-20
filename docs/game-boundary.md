# Game boundary after issue #8

The runner talks to `game.contract.GameSession`, which both the scripted fake
and native session implement. Only validated public actions reach
`apply_public_action`; the agent never receives a bridge, raw game state,
checkpoint path, or evaluator method. Synthetic fake episodes remain explicitly
labeled and never count as native evaluation evidence.

| Owner | Responsibility |
| --- | --- |
| `game.contract` | Runner/evaluator interfaces and native error taxonomy |
| `game.actions` | Public handle resolution and one action-to-RPC mapping |
| `game.state` | Native state conversion and legal-action projection inputs |
| `game.environment` | Validated runtime settings, file/identity checks, pure path conversion |
| `game.transport` | Private Windows PowerShell/RPC channel, 17-method allowlist |
| `game.session` | Owned native process, run lifecycle, checkpoints, replay entry |
| `game.replay` | Private continuation replay and divergence checks |
| `game.fake` | Scripted transport fake for application tests, not Balatro rules |
| `evidence` | Certification and source/continuation fingerprints |
| `native/patches` | Lua inspection, actions, settlement, and private dispatch |

The configured runtime path is the only source for the PowerShell launch and
checkpoint guard. The bridge passes it to Lua as `BH_RUNTIME`; the Lua dispatcher
accepts checkpoint paths only beneath that runtime. A native RPC rejection
retains its sanitized Lua code and category in private episode evidence. Public
action-rejection events remain generic; the terminal reason keeps the specific
code so infrastructure and legality failures are not collapsed.

Offline checks run with `scripts/check_offline.py` after `uv sync --locked`;
they do not launch Balatro. The issue #8 split requires new native runtime
certification before any production benchmark run or branch-restoration claim.
For repeatable Python size checks, run
`scripts/source_metrics.py --max-file-lines 399 --max-function-lines 60 --frozen-function-exception src/balatro_horizons/game/fake.py src/balatro_horizons/game`.
The exception is explicit because the byte-identical fake has a pre-existing
78-line observation method; all new functions remain below 60 lines.
