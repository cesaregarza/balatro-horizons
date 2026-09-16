---
name: balatro-play-discard
description: Choose a Balatro play or discard using the current target, remaining resources, public draw information and held effects.
---

# Choose a play or discard

Choose for the actual target and remaining resources. Treat estimated draw odds separately from the probability of surviving the whole Blind.

Find a currently available reliable clear as a baseline. Compare relevant alternatives: another supported hand family, a smaller play retaining useful held cards, a cycling play, a consumable-assisted line, or a discard toward attainable outs. Use [scoring](../balatro-scoring/SKILL.md) when needed.

Compare survival across the remaining hands, not only the next hand's mean. `remaining_target / hands_left` is orientation, not proof of viability. One hand left is a reason to optimize the final play; it is not a reason to stop using available discards or consumables. A made hand can be discarded when it cannot clear and a better draw is the only viable continuation.

Keep held Steel/Gold/Blue value and Purple discard value distinct. Kicker cards can cycle the hand or satisfy The Psychic, but may change classification, lose held value, or incur played-card penalties. Check the revealed Boss and available draw count before every decision. Growth or income farming is an option only when its survival cost is justified; the round can end before another planned hand.

When the public remaining multiset is known and draws are uniform without replacement, `P(at least one useful out in d draws) = 1 - C(N-K,d)/C(N,d)`. Expected useful count is `d*K/N`. Require `0 <= K <= N` and `0 <= d <= N`; these are draw probabilities, not complete hand/Blind survival probabilities. For `N=44,K=9,d=3`, the first expression is about 0.5058. Unknown composition requires an estimate, not invented precision. No hidden order or seed reconstruction is permitted.

For Straights, preserve viable rank windows; for Flushes, compare useful suit density and card effects; for repeated ranks, seek the supported rank. Refresh those counts after deck edits. Submit one supported action with actual IDs and observe again; optional notes can state the main risk.
