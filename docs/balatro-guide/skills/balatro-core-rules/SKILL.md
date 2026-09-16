---
name: balatro-core-rules
description: Interpret Balatro phases, resources, blind targets and ordinary victory conditions without strategy advice.
---

# Core mechanics

Use actual public counters and active effects over the defaults below. See [known limits](../../KNOWN_LIMITS.md) for specific timing and interaction uncertainties.

- A run contains Antes; an Ante normally contains Small, Big, then Boss Blind. A round is one played Blind. Clear the Ante-8 Boss to win the ordinary run; endless continuation is a separate objective.
- Accumulated score must reach the current target. Overkill does not carry forward. Exhausting viable resources normally ends the run, subject to an actual rescue effect.
- Base resources: 4 hands, 3 discards, hand size 8, $4, 5 Joker slots, 2 consumable slots. Red Deck adds a discard. Hand size and hands remaining are different quantities.
- A normal play/discard selects 1–5 held cards. A play consumes one hand; a discard consumes one discard action. Played kickers are not discarded cards. A Boss may permit submission yet award no score.
- Normal refills draw toward hand size; insufficient draw-pile cards produce a smaller refill. Used cards do not reshuffle mid-round. Start a new round with the surviving modified deck, subject to active effects.
- Small and Big may be skipped for their displayed tags. Bosses cannot ordinarily be skipped. A skip forgoes the played round and its normal cash-out/shop; a tag can grant a separate reward.
- A cleared Blind leads through settlement to a shop. Packs are modal selections; observe the actual return phase, including packs opened by tags outside shops.
- Default Blind rewards: Small $3, Big $4, regular Boss $5, Showdown $8. Red+ removes only the Small Blind's base reward. Ordinary unused hands pay $1 each; discards have no default payout.
- Interest is ordinarily $1 per full $5 of eligible cash, capped at $5. Settlement timing and special income need the actual engine ordering; see [shop rules](../balatro-economy-shop/references/shop.md).

J/Q/K are faces; Ace is not. Numbered cards are 2–10. “Scoring” is a subset of played cards; “held” means cards left in hand during resolution. Chips and Mult are scoring accumulators; money is a purchase resource. Hand levels persist separately from poker-hand precedence. A debuff and a concealed identity are different states.

Read [hand recognition and scoring](../balatro-scoring/references/mechanics.md) for classification, [decks/stakes](../balatro-decks-stakes/references/decks-stakes.md) for target tables and modifiers, and [tags](../balatro-economy-shop/references/tags.md) for skip rewards. No complete score preview is assumed.
