---
name: balatro-scoring
description: Estimate Balatro hand scores from public cards and effects while distinguishing exact arithmetic from uncertain mechanics.
---

# Estimate a candidate score

Use [mechanics](references/mechanics.md) for phase order, hand precedence and examples; load [hand values](references/hand-values.md) only when a base value or increment is needed.

Establish selected cards in physical order, held cards, current hand values, active Boss, Joker effects/counters/order and copy targets. First classify the hand and scoring subset. Then apply known pre-scoring changes, scored-card events/retriggers, held effects, independent Jokers, and final modifiers in their actual phases.

Compare with `max(0, target - accumulated_score)`. Keep money, growth and permanent card changes separate from this score. A hand label/base Chips-and-Mult display is not a complete deterministic preview, and the current arithmetic helper is not a scorer.

Use **exact arithmetic under stated assumptions**, **justified bound**, **estimate**, or **unknown** accurately. Missing hooks, hidden identities, RNG or unverified rounding prevent a native-exact claim. A no-luck case is a lower bound only if all omitted outcomes and interactions can only improve it.

For ordering, compare meaningful alternatives within controllable timing. Additive Mult before multiplicative Mult helps in a simple same-phase case; first-card retriggers, copying and destructive effects can change the best order. Reorder through the host's actual action and re-observe before playing. Optionally report the score assumption and one decisive difference; a full internal reasoning trace is not required.
