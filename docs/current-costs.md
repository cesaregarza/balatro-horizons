# Current game costs and card modifiers

The browser labels visible editions and stickers beside card names, including
purchase/pack-choice titles, direct pickup summaries, sales, shop offers and the
owned-card board. The current public Drunkard record renders as `Drunkard (P · ∞)`.
The expandable legend defines F (foil), H (holographic), P (polychrome), N
(negative), ∞ (eternal), X with a recorded remaining-round count (perishable), R
(rental), and D (debuffed). False flags, ordinary ability descriptions and hidden
identities do not acquire markers. Name-only indirect gains are not assigned a
modifier based on an ambiguous name match. The underlying journals and JSON/JSONL
exports retain their original public values.

`configs/prompts/ALWAYS-LOADED.md` now states that ordinary card chips and
"when scored" effects come from the scoring cards: A-A-A-K-Q scored as Three of a
Kind scores the Aces; the added King and Queen contribute neither card chips nor
"when scored" effects. The existing public-effect override caveat applies. The
file remains within its unchanged 1,024-byte limit, and `tools-v5.txt` embeds the
same text. Existing episodes keep their frozen
prompt; the new instruction is used by newly created tools_v5 episodes.

## Current cost summary

`agents/costs.py` derives `current_costs` version `public_costs_v2` from the same
canonical public observation as the board. It includes that observation's ID,
cash balance, credit limit, positive-price spending headroom, and inventory usage
and capacity. Phase-specific entries contain shop/boss reroll quotes, offer
prices, and quoted sale proceeds. Unknown prices remain JSON `null` through
projection, validation and final provider serialization; they do not become zero
or the string `"None"`.

Affordability uses the action validator's shared admission rule. Separate
`purchase_modes` checks run that validator against acquisition, buy-and-use or
pack choice with empty targets. Their status is `legal`, `unavailable`, `unknown`,
or `requires_targets`, with the validator's rejection code when applicable. An
affordable consumable can therefore be unavailable to store but usable immediately
with a valid target selection. Zero-cost actions preserve the validator's existing
behavior even without positive cash headroom. Visible owned items include their
sale eligibility, so an eternal Joker's sale quote is not presented as an available
sale. Concealed owned identities and their sale prices are excluded.

The next reroll quote comes from `shop_reroll_cost`. Owning Chaos does not establish
that its free reroll is still available. The prompt now explicitly distinguishes
an effect description from a remaining entitlement and directs the agent to
recheck the current quote after every settled action, including pack returns.
`free_rerolls_remaining` is `null`: the current adapter does not independently
expose that count. No future prices or purchases are recommended.

A zero quote means free **upfront**. An explicitly visible `rental: True` flag is
also represented in `recorded_obligations`, including on a zero-price offer or an
owned Joker. The effective charge and timing remain `null` because this adapter
does not instrument them separately. The summary does not infer a rate, duration,
or lifetime cost from the card's name or from changes in cash.

## Public transaction receipts

After a settled purchase, pack choice, sale or reroll, `last_action.transaction`
records a `public_transaction_v1` receipt. Its quote comes from the pre-action
public observation; balances come from the public states before and after the
action. The surrounding last-action record identifies the action, selected
objects, and both observation IDs.

`quoted_cash_charge` and `quoted_cash_proceeds` are separate from
`actual_cash_charge` and `actual_cash_proceeds`. Actual charges/proceeds remain
`null` with `actual_charge_source: not_observed` until separate native
instrumentation can measure them. Net cash change can include other triggered
effects and is not labeled as the transaction fee or used to assert a quote
mismatch. Missing balances also leave the net change unknown.

Receipts travel with the public observation through journals, checkpoints,
provider requests, and full public JSON exports. Prospective review reveals a
receipt only with the corresponding post-action transition. Old records without
the optional receipt still parse, and existing journals are not rewritten.

## Context and validation

Only tools_v5 receives this new field. Both providers serialize it after the
observation in the dynamic user message. Tool definitions and the stable
developer/system instructions do not contain the prices; OpenAI's existing
explicit breakpoint remains before the dynamic message. This follows
[OpenAI's prefix and breakpoint guidance](https://developers.openai.com/api/docs/guides/prompt-caching).
Tests inspect final serialized requests for both providers: Chaos with an
intervening purchase (decisions 14–17), Chaos with a pack visit (25–29), and a
zero-upfront Rental/Polychrome/Perishable Banner (162–163). The checked-in fixture
uses recorded public numeric quotes and actions with synthetic remaining board
fields; it is not a native replay or evidence of continuation fidelity. The
recorded requests already contained the paid reroll quote at decisions 16 and
28; this change makes it explicit, rather than correcting a missing quote.

Tests also cover capacity and targeted purchase modes, cash versus credit,
concealed-effect noninterference, unknown quotes, receipts with other cash
effects, old-record compatibility, persisted receipts and temporal review.
Tools and cached instructions remain identical across price/state changes;
helper continuations retain the quote for their unchanged observation. The new
wording intentionally changes the frozen prompt for newly created episodes.

Validation: 350 Python tests and 18 browser tests passed; nine native evidence
gates were skipped. Ruff, prompt synchronization, the Node 22.12.0 frontend build,
and the whitespace check passed. The built JavaScript and CSS asset hashes match
the deployed dashboard, since the frontend change only makes the offer-price
type nullable to match the public contract.

## Native deployment — 2026-09-16

The cost-summary implementation was installed while the worker was idle and the
user service was stopped. A fresh matching capability certificate was activated
only after one full native release suite passed: **22 planned native launches,
no retries and zero paid calls**. Windows instrumentation and the environment
lock were unchanged; no mod reinstall was needed.

The suite passed fresh-profile checks for Red/White and Red/Gold, all 14 action
families, rejection and ambiguous-timeout handling, terminal detection, ordering,
three repetitions of each seed-prefix proof, three direct-save restorations at
the tested Gold decision, a separate branch with an unchanged parent, and
settlement visibility checks. This is calibration and assisted diagnostic
evidence, not a model-performance result. The certificate remains bounded to the
tested phases and decisions; headless execution and acceleration remain disabled.

`scripts/verify_cost_evidence.py` additionally checked 29 public observations from
the completed native action fixture, producing 58 serialized provider contexts
and checking 11 purchase/pack/reroll/sale receipts. The evaluator fixture omits
`last_action`, so these native receipts were reconstructed; persisted receipts
were checked in the offline runner/review tests. This checker launches no game
and uses no provider transport or private state. It does not measure actual
transaction charges, live cache hits or improved model decisions.

`finalize_evidence.py` passed, both `bh doctor` configurations reported no blockers,
and 23 focused native-evidence/adapter checks passed after activation. Five focused
deployment-helper and cost-evidence tests also passed. The workbench was restarted;
localhost and the existing Tailscale route returned HTTP 200 with an idle worker.
Operator spending settings were byte-identical to their pre-deployment snapshot.

Evidence remains in the local, ignored `reports/verification/` directory:
`native-release.json`, `native-evidence.json`, `native-settlement.json`, and
`native-current-costs.json`. The action fixture is
`899a2b7c316c402fbf1d75a5d451c277`; the Gold parent is
`76e151b48ae6443ba18c5138c9c092a5`, and its branch is
`69e1bafaab154e2fa58f955d3a337222`.

The active implementation fingerprint is
`1ed4f99e03c8b6e0cd6830b6b2b974706ef53912cb2ad764c34a853a6ed9be76`;
the environment fingerprint is
`03ffd235312449ae6c2a74d9d33093d3fd0f6b4691ab13c34371fe9f93166dd3`.
The prior source and certificate selections are backed up under
`private/deployments/current-costs-d908283/`. Existing episode journals retain
their frozen prompts and public observations.

For future deployment preparation, invoke `scripts/install_candidate.py` with the
repository's `.venv/bin/python`. Its `--prepare --root <live> --candidate
<candidate> --manifest <new-file>` options write a checksum manifest from clean
Linux checkouts without installing. The install/rollback commands consume that
manifest after idle checks and service shutdown. On this host the service is
`systemctl --user ... balatro-horizons.service`.
