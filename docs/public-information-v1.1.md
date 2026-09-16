# Public information contract 1.1

For subsequent deployment and native verification, see the
[September 16 release handoff](release-2026-09-16.md). The implementation-stage
status below is retained as historical evidence.

This is handoff work package B1, implemented separately on `fix/public-information`
on top of budget-fix commit `2ab1730`. The running workbench and dedicated Windows
runtime were not updated. It changes public observations and their presentation;
it does not implement provider continuations, new memory policy, a hand-preview
oracle, or research scoring.

## What the model receives

- Shop and standard-pack playing cards retain visible rank, suit, enhancement,
  price, and acquisition constraints. A generic native ability label such as
  “Base Card” or “Bonus Card” no longer replaces their visible identity.
- Concealed offers have opaque encounter handles, generic labels, and no rank,
  suit, enhancement, or hidden targeting details. Handles are renewed across
  concealment. Displayed price and acquire affordance remain public. Stone Cards
  and other cards with a replaced/absent rank or suit do not expose an underlying
  base identity; the native deck-composition extractor uses `No suit:No rank`
  for the corresponding Stone Card count.
- Blinds carry the adapter's reported status (`SELECT`, `CURRENT`, `UPCOMING`,
  `DEFEATED`, or `SKIPPED`; missing status is `UNKNOWN`). Blind effects and
  `skip_reward` are separate. Each skip reward explicitly requires
  `acquisition_condition: skip_this_blind`; its description supplies the native
  activation conditions. Fighting the blind does not acquire that reward.
  The current blind also carries the observed `disabled` flag so, for example,
  disabling a Boss does not leave its description presented as an enabled effect.
  Outside an active hand or for older captures, that flag is unknown (`null`).
- `owned_vouchers` and `pending_tags` contain native-visible labels and resolved
  descriptions. Pending tags come from observed acquired tag objects, excluding
  triggered ones. They are not inferred from offered skip rewards. Hidden tags
  retain a generic label. Legacy `persistent_effects` remains readable.
- `last_action` describes the transition from the preceding public observation:
  selected labels, added/removed public objects, changed resources, progress,
  hand levels, counters, effects, ordering, vouchers, and pending tags. It uses
  pre-action labels for departed handles. Changes are before/after observations,
  not causal attribution or a final-score prediction. Agent notes, hand-name
  claims, and memory updates are not inputs to this calculation.

The observation journal and checkpoints preserve this feedback. An ordinary
branch inherits the known pre-decision feedback and then computes its own
transitions. Prospective review still withholds the current action's result until
consequences are revealed. The board distinguishes skip offers from owned effects
and escapes all description text as React text nodes.

## Version and compatibility

New canonical observations declare `schema_version: 1.1`. Compact provider views
retain that identity as `public_contract_version: 1.1`; last-action feedback is
separately marked `public_delta_v1` with source `observed_public_states`.
The existing `operate_v1` and `tools_v2`/`v3`/`v4` operations remain available.
Inspection gains the corresponding public sections, including `last_action`.
This is an input representation change and must be distinguished in comparisons
with older runs; it is not evidence of improved gameplay.

Historical 1.0 observations remain readable. Missing voucher/tag collections and
last-action feedback default to unknown (`null`), and missing blind status is
`UNKNOWN`. Old journals are not rewritten or enriched retrospectively. Normalizing
older captured engine states without the new instrumentation can only preserve
their existing keys/descriptions; it cannot recover missing native text.

## Native extraction and verification limits

The installer already copies all `native/patches/*.lua`; the entrypoint now loads
`horizons_public.lua`. Descriptions use the native `generate_card_ui` builder and
only its main text. Generated UI objects are cleaned up; auxiliary tooltip nodes
are not serialized. The pinned Steamodded patch exposes localization variables
through `Tag:get_uibox_table(nil, true)`. Acquired tags use their real objects;
offered rewards use plain description proxies with the visible Orbital hand,
without constructing a Tag or advancing RNG. The existing private tag-key list
used in continuation fingerprints is retained separately.

This was checked against the locally cached pinned BalatroBot extractor and
Steamodded `lovely/tag.toml`, `lovely/enhancement.toml`, `src/utils.lua`, and
`src/game_object.lua`. Lua tests execute the project module against stubbed game/UI
objects, prohibit Card/Tag construction and RNG calls, check visibility and
description forwarding, and compile every project Lua patch as Lua 5.1.
[Lupa](https://pypi.org/project/lupa/) 2.8 is a locked development-only dependency
for these Linux tests, not a replacement for the native game runtime.

Native tooltip parity, frame stability, RNG nonmutation in the real game,
concealed-offer affordances, and fresh-process replay are still unverified for
this patch. The stubbed tests cannot establish those properties. Deployment needs
authorized installation of the changed instrumentation and the normal native
release gates, including representative voucher/tag/pack/Stone Card UI checks.
No game mechanic, score, price, RNG, or action-execution function was edited.
No certificate or authorization gate was bypassed.

## Validation

The offline Python suite passed: **258 passed, 9 skipped**. Eight skipped checks
require native evidence; one requires the pinned vendor checkout in this
worktree. Existing Starlette/AnyIO deprecation warnings remain. Ruff and Git
whitespace checks passed. The tests exercise actual Lua extraction with stubbed
native objects, observation projection, all four harness interfaces, final
serialized requests for both providers, branch inheritance, exports, and
prospective review. These are offline boundary checks, not native results.
The frontend production build passed. All 12 browser cases passed: 11 in the
full run and the remaining new case in a focused rerun after fixing its
exact-text locator to allow the displayed separator. No application change
was needed for that rerun.
No paid-provider requests or Windows game launches were performed.

The browser checks use an isolated test server on port 8766 and worktree-local
data. Screenshot destinations now use Playwright's per-test output directory so
testing a worktree does not write images into the live checkout.

The repeated offline verification workflow now has one entrypoint:

```bash
/path/to/checkout/scripts/check_offline.py --web
```

It requires previously installed locked dependencies, Node LTS, and Playwright's
browser; it does not install anything. The script strips provider API keys from
child processes and stops at the first failed check. Its tests cover checkout
selection, web opt-in, credential removal, and failure propagation. Running it
does not authorize native validation or a paid smoke run.
