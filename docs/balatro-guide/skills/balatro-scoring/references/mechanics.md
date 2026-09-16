# Scoring mechanics reference

Use this phase model to organize score estimates. For uncommon hooks or close rounding thresholds, consult [known limits](../../../KNOWN_LIMITS.md).

## Recognition and scoring subset

Read [hand values](hand-values.md) for the 12-hand/Planet table, precedence, levels and Four Fingers example. The game classifies the highest recognized category, not the hand with the most valuable current level. Royal Flush uses the Straight Flush track. “Contains Pair” can differ from “is Pair.”

Plain scored cards add their rank for 2–10, 10 for J/Q/K and 11 for Ace. Only the recognized scoring subset normally contributes; played kickers do not become discard events. Stone and Splash can add scoring cards. Debuffed cards can still participate in classification while contributing no ordinary card effects. Wild loses all-suit behavior when debuffed; Stone's detailed debuff/identity interaction is a matching-build edge case.

## Abbreviated event order

| Phase | Events to model | Material limit |
| --- | --- | --- |
| Boss / pre-scoring | Restrictions, hand-level changes, hand-play hooks, enhancement changes | Relative hooks can matter; do not infer a complete ordering from this table |
| Scored cards, physical left to right | Rank chips, enhancement/edition/seal effects, eligible per-card Jokers and retriggers | Retriggers repeat eligible card events, not the hand base or whole independent Joker pass |
| Held cards | Steel, relevant held-card Jokers and eligible repeats | Played and held roles differ |
| Independent Jokers, left to right | Foil/Holographic before own independent effect; Polychrome after; Joker-dependent hooks/copies | Copies and other-Joker hooks can intervene |
| Final effects | Observatory for matching held Planets; deck finalization; score added to round | Exact rounding and uncommon interactions require engine evidence |
| Later events | After-hand effects, destruction and round-end triggers as applicable | Not a claim of exact mutual ordering or cash-out timing |

Glass/card Polychrome/Steel and per-card multiplication occur before independent-Joker additions. Moving an independent Joker left cannot move it into the card phase. Separate retrigger sources add extra activations: initial 1 + Red Seal 1 + two additional repeats = 4 activations, not 6.

Do not conflate a scaling value change with when that value contributes score. A Joker can hook several phases. “When discarded,” “when sold,” “when a card is destroyed,” “when rerolling,” and “at end of round” are separate events; read each effect. Red Seal does not automatically repeat every possible event.

## Arithmetic examples, conditional on stated effects

- Pair of Kings, base 10 Chips/2 Mult: total Chips 30. Synthetic independent +4 then ×1.5 gives 270; reversing gives 210.
- Pair of 9s, one Mult then one Glass, independent +10: `(10+9+9)*((2+4)*2+10)=616`. Glass first gives 504.
- Four 10s, base 60/7, one Foil, held Steel King, independent +20: `150*(7*1.5+20)=4575`. These assumptions exclude all other effects.
- Lone Mult-enhanced Red-sealed Ace: one extra eligible scoring activation gives `(5+11+11)*(1+4+4)=243`.
- Flush level 4, card chips 34: `(80+34)*10=1140`. Poker rank alone is not a score forecast.

For fixed nonnegative addition `a` and multiplier `x>1`, same-phase addition first improves Mult by `a*(x-1)`. This algebra does not establish the best arrangement when first-card selection or copies change which effects occur.

Ordinary score is Chips × Mult with engine rounding. Plasma balances Chips and Mult from their sum before scoring, approximately `((C+M)/2)^2`. Exact flooring for odd or fractional sums remains unresolved. For example, sum 110 gives 3025 under either common rounding interpretation, while sum 111 depends on where flooring occurs. Compare gains to the effective sum; adding to the lower component is not inherently better.

References: [Poker Hands](https://balatrogame.fandom.com/wiki/Poker_Hands), [Activation Sequence](https://balatrogame.fandom.com/wiki/Guide%3A_Activation_Sequence), [Card Modifiers](https://balatrogame.fandom.com/wiki/Card_Modifiers), [Plasma Deck](https://balatrogame.fandom.com/wiki/Plasma_Deck).
