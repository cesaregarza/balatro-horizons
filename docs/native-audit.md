# Native action, visibility, and runtime audit

The baseline is BalatroBot `e7c6db8a9ad88318f6e4128eefd6e61aafc94885` (1.5.2), Balatro 1.0.1o-FULL / LÖVE 11.5.0, Lovely 0.9.0, and Steamodded 26.829.0 (`39182f0cc7b1af86d3d3d6afc5422661a07b4312`). The installer computes exact game, DLL, bridge, and mod-tree checksums into the private environment lock. Startup verifies both disk content and the manifest loaded by the running game. A fresh instance nonce prevents accidental attachment to an earlier process; shutdown is restricted to that same instance. Default per-profile save inputs are blocked as well as profile/settings inputs, so previous isolated saves cannot influence the new-run selection UI.

## Strategic operations

| Public action | Native execution | Validation / evidence case |
| --- | --- | --- |
| Select / skip blind | Native BalatroBot select/skip | Current blind handle; skip rewards remain separate decisions |
| Play / discard | Native highlighted-card callbacks | Current distinct card IDs, native maximum, forced selection; no automatic sorting |
| Reorder | Patched BalatroBot rearrange | Full permutation; hand during hand selection / packs, owned cards also in shop / blind selection / cash-out |
| Buy / buy-and-use | Shop purchase or native use callback | Price plus credit, edition-aware capacity, explicit mode and targets |
| Sell | Native sell callback | Owned current handle; hidden sellability stays unknown |
| Use consumable | Native use callback | Explicit target IDs/count, including non-shop use |
| Reroll shop | Native shop reroll | Visible cost and credit limit |
| Reroll boss | `G.FUNCS.reroll_boss` | Directors Cut / Retcon, current spend and per-ante availability |
| Choose pack | Native use callback | Remaining choices and targets; Mega packs remain open after first pick |
| Skip pack | Native skip-booster callback | Wait for actual settled return, including blind-selection reward packs |
| Cash out / leave shop | Native cash-out / next-round | Explicit actor operation, current phase |

Narrow project patches address the optional `viewed_back` removed by newer Steamodded, fresh-profile tutorial completion, negative-edition capacity, buy-and-use at full consumable capacity, boss rerolls, targeted consumables, multi-choice packs, and completion predicates. The game mechanics used by ordinary episodes remain native. Named evaluator fixtures are authenticated, require a separate calibration launch flag, and are permanently excluded from evaluation.

Every operation has a persisted intent before execution and a unique request ID. A known committed request can be acknowledged without replaying its mutation. Reusing an ID for different parameters is rejected. Unknown status after transport loss terminates the episode as infrastructure failure. Before a consumable use callback, the adapter checks the native availability predicate with the explicitly requested targets and restores the temporary validation selection; a rejected target queues no effects. The per-process game ledger is not claimed to provide cross-process exactly-once execution; crash recovery never silently resumes an ambiguous action.

## Game/transport hops and error names

`game/contract.py` declares the runner-facing session and evaluator-only methods.
One action follows this chain; no bridge method is exposed as an agent tool:

| Hop | Owner | Boundary |
| --- | --- | --- |
| 1 | `runner.py` | Persist validated public action intent and request ID |
| 2 | `game/actions.py` | Resolve handles and select one allowlisted RPC |
| 3 | `game/session.py` | Own the process/lease and await a settled observation |
| 4 | `game/transport.py` | Send authenticated JSON-RPC through PowerShell |
| 5 | `native/bridge.ps1` | Forward only 17 permitted methods over loopback HTTP |
| 6 | `native/patches/dispatch.lua` | Authenticate, serialize writes, and retain request status |
| 7 | `native/patches/action.lua` | Apply a validated native action callback |
| 8 | `native/patches/settle.lua` | Complete after 30 transitions and 10 ready frames |
| 9 | `native/patches/inspect.lua` | Return the next private native inspection |
| 10 | `game/state` → `observations` | Normalize, then strictly project public information |

| Lua error class | Examples | Python/runner outcome |
| --- | --- | --- |
| `NOT_ALLOWED` | `UNAFFORDABLE`, `UNKNOWN_CARD` | `NativeRejected` / `INVALID_EVALUATION` |
| `INFRASTRUCTURE` | `BUSY`, `NOT_READY`, `ACTION_STATUS_UNKNOWN` | `NativeFailure` / `INFRASTRUCTURE_FAILURE` |
| `HARNESS_FAULT` | `UNAUTHORIZED`, `METHOD_FORBIDDEN`, `PATH_FORBIDDEN` | `NativeFailure` / `INFRASTRUCTURE_FAILURE` |

The sanitized Lua `message` code and `name` are recorded only in private episode
error evidence; the terminal reason carries the specific code. This taxonomy
is an executable change. Existing native certificates do not authorize runs
with this source/runtime fingerprint; desktop re-certification follows in #9.
For repeatable Python size checks, run
`scripts/source_metrics.py --max-file-lines 399 --max-function-lines 60 --frozen-function-exception src/balatro_horizons/game/fake.py src/balatro_horizons/game`.
The exception is explicit because the byte-identical fake retains its
pre-existing 78-line observation method; all new game functions remain below
60 lines.

## Reorder phase regression — 2026-09-15

Terra's recorded run `a0cfdd88d48d4453821e67ae1ee5f2c2` requested a legal Joker
permutation at blind selection. The public interface advertised it, but the pinned
BalatroBot endpoint admitted only hand selection, shops, and Steamodded packs.
Its two owned-card completion predicates had the same limitation. The original
run remains unchanged and retains its invalid-evaluation outcome.

`scripts/native_patches.py` verifies the exact upstream source checksum before
extending both admission and completion to blind selection and round evaluation.
Hand ordering retains its narrower phase guard. Public constraints, validation,
named model tools, and human controls use the same eligible areas. No move is
substituted, and a request ID still identifies one execution.

The unpaid `native_acceptance.py --case reorder` fixture recreates the six-Joker
inventory and swaps Abstract Joker ahead of The Order at blind selection. It
checks exact order, unchanged phase/resources, other supported phases, and an
identical request-ID replay with no second mutation. Release activation requires
matching, passing `native-reorder.json` evidence. This fixture is permanently
excluded from performance scores; offline tests alone do not certify the fix.

The [native regression record](../reports/verification/native-reorder-09ffcadf5cdb4ed8a537ef9119ad456a.json)
passed all ten checks, including the six-Joker blind-selection permutation and
duplicate-request non-mutation. The episode summary records zero provider calls;
its manifest excludes the fixture from evaluation.

## Public information

Observations are constructed twice: a native-specific normalizer followed by a strict Pydantic allowlist. Visible counters, resource totals, prices, capacities, item order, card effects, hand levels, and current blind previews are public. Raw engine objects and future draw order remain private.

Face-down cards lose names, rank, suit, effects, counters, price and private usability. Their opaque handles are issued by public encounter order with a random episode salt, never by hashing native card identity. Concealment and disappearance evict stable mappings, preventing reconnection through a concealed shuffle. Revealed cards remain trackable only while a player can track them. Tests compare complete public observations and legal constraints after hidden-state changes.

Native `G.UIDEF.view_deck` in the licensed `functions/UI_definitions.lua` sorts the owned playing cards for the player's deck view. The adapter exposes aggregate owned rank/suit composition, labeled `native_deck_view`, and remaining count. It does not publish draw order or an unplayed identity histogram. The proprietary source is inspected locally with `scripts/inspect_game.py` and is not copied into this repository.

## BlindDeck audit

Reviewed [BlindDeck gamestate](https://github.com/FFFishes7/blinddeck/blob/8015c448d0a97e4f9d180ae6f1192412cc308871/src/lua/utils/gamestate.lua), [pack handling](https://github.com/FFFishes7/blinddeck/blob/8015c448d0a97e4f9d180ae6f1192412cc308871/src/lua/endpoints/pack.lua), and [boss reroll](https://github.com/FFFishes7/blinddeck/blob/8015c448d0a97e4f9d180ae6f1192412cc308871/src/lua/endpoints/reroll_boss.lua) at that pinned revision.

Its hidden-card extraction suppresses identity fields but retains native sort IDs and cost. Horizons uses its own stricter public projection because those residual fields can link concealed objects or reveal identity. Its pack implementation identifies the important two-choice and shop-versus-blind-selection return cases; these informed the project adapter's independently tested completion handling. It also documents hand-order-sensitive targeting, including Death. Horizons uses the native current left-to-right hand order; a target list selects cards, and explicit reorder operations change that order. It does not rewrite hand positions to give a target list a different semantic meaning.

No BlindDeck seed-query, prediction, card-generation, or unrestricted debug helper is exposed. The fork is a source-audit reference, not an additional runtime mod.

## Supported evidence boundaries

The private `environment.lock.json`, runtime audit, individual episode journals, and immutable replay certificate records identify the tested environment. Synthetic tests never confer native certification. Native saves may diverge at phases they appear to support; each failed check records a private first-divergence artifact. A seed-prefix certificate verifies the actual replayed prefix and suffix in fresh processes. A branch carries that certificate and its immutable parent reference.

Visible speed-1 execution is supported. Headless and accelerated execution are disabled until separately compared against the same visible action traces. Live provider model compatibility and actual paid usage require separately authorized smoke tests. See the generated verification report for the precise current results.


## Startup diagnostics

For an idle runtime with a handshake timeout, use:

```bash
uv run scripts/diagnose_native_startup.py
uv run scripts/diagnose_native_startup.py --restart
```

The first command probes RPC and reports allowlisted launch/log metadata; it emits
no seeds, raw game state, process nonces or log contents. The explicit `--restart`
mode requires a matching native certificate, holds the native worker lock, stops
only the registered dedicated runtime through the fixed bridge, verifies a fresh
identity handshake, then stops its own diagnostic process. Neither command calls
a model. A failed probe or restart exits nonzero. Windows-path authorization is
still required under the workspace rules.

The diagnostic helper does not change native source, repair profiles, or grant
capabilities. A successful handshake is startup evidence only, not replay or
restoration certification.

The live workbench can be checked separately with
`uv run scripts/diagnose_native_startup.py --workbench --preset smoke`.
This starts a human-control diagnostic through the actual browser API, waits for
a native decision, then aborts it without committing a move or calling a model.
The episode remains in the journal and is excluded from evaluation.

### Web-service launch context repair — 2026-09-15

A direct shell launch loaded instrumentation, while the service launch timed out
without a new Lovely log. The service lacked the shell's Windows user-profile
and WSL session variables. Applying a narrow environment allowlist to that service
restored modded startup through the actual browser API. The check reached
`BLIND_SELECT`, passed the native identity/profile checks, and was deliberately
aborted with zero provider calls. See `reports/verification/startup-recovery.json`.
Earlier failed attempts retain their original infrastructure-failure journals.

From a working Windows-connected WSL shell, after starting the idle service:

```bash
uv run scripts/configure_workbench_session.py
uv run scripts/configure_workbench_session.py --apply
```

The first command previews variable names only. The second writes a service-only
runtime drop-in and restarts `balatro-horizons.service`. It copies only available
Windows profile/system variables and WSL session context, constructs a filtered
`WSLENV`, and preserves the existing credential file and budgets. It does not
import the shell's credentials, proxies or general environment. Both repair and
restart diagnostics reject a busy native worker. The runtime drop-in is ephemeral;
reapply from the current working shell when recreating the service after WSL
restarts. Native code, mods and capability certificates were unchanged by this fix.
