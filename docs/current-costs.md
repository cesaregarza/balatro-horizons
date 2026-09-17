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

## Staged cost summary

`agents/costs.py` derives `current_costs` only from the current canonical public
observation. It supplies game-money balance and credit limit, current shop/boss
reroll quotes in the relevant phase, visible purchase or pack-choice prices and
sale proceeds for visible, sellable owned cards. Purchase modes and action
constraints still apply. A purchase or reroll is free only when its displayed cost
is zero. A null quote remains unknown; it is never converted to zero. Hidden sale
identities and prices are not added to this summary.

The next reroll quote comes from `shop_reroll_cost`, not a Joker description or
an assumption that a previously free reroll remains free. The pinned engine sets
that displayed quote to zero while a free reroll is available. The summary does
not simulate later prices, recommend a purchase, or invent a separate economy
objective.

Only tools_v5 receives this new field. Both providers serialize it after the
observation in the dynamic user message. Tool definitions and the stable
developer/system instructions do not contain the prices; OpenAI's existing
explicit breakpoint remains before the dynamic message. This follows
[OpenAI's prefix and breakpoint guidance](https://developers.openai.com/api/docs/guides/prompt-caching).
Tests verify identical tool catalogs and stable instruction blocks when a reroll
quote changes from zero to five, with Chaos still present. Helper continuations
retain the quote for the same unchanged observation. The independently requested
scoring reminder intentionally changes the persistent prefix for new runs.

Validation: 322 offline Python tests passed, nine native evidence gates skipped;
Ruff, prompt sync, and the Node 22.12.0 frontend build passed. The two focused
modifier checks plus the four existing decision-explorer/public-information
browser checks passed on an isolated test server. These tests launched no Balatro
processes and made no paid calls. They do not establish a live cache-hit rate or
an effect on model decisions.

The browser changes and scoring reminder were published without a backend restart.
The cost-summary implementation remains staged until the worker is idle and a
fresh matching native capability certificate can be produced. Its Python source
changes the conservative harness fingerprint; the existing certificate is not
rewritten or treated as proof for the new source.
