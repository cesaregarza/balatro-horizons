---
name: balatro-orchestrator
description: Route a Balatro Horizons decision or rules lookup to the relevant module and references.
---

# Route the current question

Play to complete the ordinary Ante-8 native run. Use current public state, active effects and the host's actual tool schemas. When a handbook default differs from the observed game, use the observed value; keep missing information unknown.

For a rule or numeric value, read the relevant mechanics reference. For an action choice, load its decision skill and only the references needed. Do not load the whole guide each turn.

| Need | Neutral rules/reference | Decision skill |
| --- | --- | --- |
| Phase, counters, terminology | [Core rules](../balatro-core-rules/SKILL.md) | This router |
| Hand recognition and timing | [Scoring rules](../balatro-scoring/references/mechanics.md) | [Scoring](../balatro-scoring/SKILL.md) |
| Play or discard | Scoring rules above | [Play/discard](../balatro-play-discard/SKILL.md) |
| Joker effects, copies, ordering | [Joker mechanics](../balatro-jokers/references/mechanics.md) | [Jokers](../balatro-jokers/SKILL.md) |
| Prices, packs, vouchers, tags | [Shop rules](../balatro-economy-shop/references/shop.md), [tags](../balatro-economy-shop/references/tags.md) | [Economy/shop](../balatro-economy-shop/SKILL.md) |
| Tarot, Planet, Spectral | [Consumable catalog](../balatro-consumables/references/catalog.md) | [Consumables](../balatro-consumables/SKILL.md) |
| Enhancements, editions, seals | [Modifiers](../balatro-deck-building/references/modifiers.md) | [Deck building](../balatro-deck-building/SKILL.md) |
| Revealed Boss effect | [Boss catalog](../balatro-boss-blinds/references/bosses.md) | [Boss preparation](../balatro-boss-blinds/SKILL.md) |
| Deck/stake modifiers | [Decks and stakes](../balatro-decks-stakes/references/decks-stakes.md) | [Adaptation](../balatro-decks-stakes/SKILL.md) |
| Build direction or investment | No additional mechanics required | [Run strategy](../balatro-run-strategy/SKILL.md) |
| Unclear result or action semantics | [Harness mapping](references/harness.md) | [Validation](../balatro-validation/SKILL.md) |

Read the current settled public state, choose one supported action, and re-observe after mutation. A short optional decision note can identify the decisive tradeoff or uncertainty; do not require a step-by-step reasoning transcript or add fields absent from the active schema. Public IDs do not reveal hidden ranks, deck order, RNG, or future outcomes.

Read [harness tools](references/harness.md) for supported operations and [known limits](../../KNOWN_LIMITS.md) when an unresolved mechanic affects the decision.
