# Hand recognition and level values

Rules baseline: Balatro 1.0.1. Use observed hand values when levels or active effects change these defaults.[^hands][^planets][^patch]

## Recognition, highest priority first

Flush Five; Flush House; Five of a Kind; Straight Flush; Four of a Kind; Full House; Flush; Straight; Three of a Kind; Two Pair; Pair; High Card. Royal Flush is the Ace-high Straight Flush label, not a separate hand-level track.[^hands]

| Hand | Rank/suit pattern without rule-changing Jokers | Level-1 Chips | Level-1 Mult | Chips per level | Mult per level | Planet |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| High Card | No higher category; highest rank scores | 5 | 1 | 10 | 1 | Pluto |
| Pair | Two matching ranks | 10 | 2 | 15 | 1 | Mercury |
| Two Pair | Two different rank pairs | 20 | 2 | 20 | 1 | Uranus |
| Three of a Kind | Three matching ranks | 30 | 3 | 20 | 2 | Venus |
| Straight | Five consecutive distinct ranks | 30 | 4 | 30 | 3 | Saturn |
| Flush | Five cards of one suit | 35 | 4 | 15 | 2 | Jupiter |
| Full House | Three of one rank plus two of another | 40 | 4 | 25 | 2 | Earth |
| Four of a Kind | Four matching ranks | 60 | 7 | 30 | 3 | Mars |
| Straight Flush | Both Straight and Flush | 100 | 8 | 40 | 4 | Neptune |
| Five of a Kind | Five matching ranks | 120 | 12 | 35 | 3 | Planet X |
| Flush House | Both Full House and Flush | 140 | 14 | 40 | 4 | Ceres |
| Flush Five | Both Five of a Kind and Flush | 160 | 16 | 50 | 3 | Eris |

Without additional base-value changes, `C(L)=C(1)+(L-1)*delta_C` and `M(L)=M(1)+(L-1)*delta_M`. Royal Flush uses Neptune and Straight Flush values. Secret Planets become eligible in ordinary generation after their hand has been played in that run; Black Hole can upgrade those hands before they are revealed.[^planets]

## Recognition cautions

An Ace may be low in `A,2,3,4,5` or high in `10,J,Q,K,A`; a Straight cannot wrap through `Q,K,A,2,3`. Suits do not break score ties. Aces are not face cards. For a Pair or Four of a Kind, unused kickers are played but normally do not score. A Full House can satisfy a Joker that asks whether a Pair is contained; do not equate every "contains" condition with the final hand label. Four identical ranks do not form Two Pair, which needs two different ranks.[^hands]

Stone ignores its underlying rank/suit for hand formation and is an extra scoring card when played, unless debuffed. Wild changes suit membership, not rank. Do not use Wild as an arbitrary missing Straight rank.[^mods]

Four Fingers reduces Straight/Flush requirements to four. A five-card set can contain a four-card Straight and a different four-card Flush and classify as Straight Flush. Example: `3S,4S,5H,6S,KS`; the Straight uses 3–6 and the Flush uses 3S,4S,6S,KS. Do not replace the game's classifier with a conventional five-card poker library for this case.[^four]

Shortcut permits gaps of one missing rank between consecutive selected ranks, still without Ace wrapping. Other rule-changing effects must be read from the active state. Do not extend a baseline classifier to unknown mods by guesswork.[^shortcut]

## Arithmetic checks

Level-2 Pair has 25 base Chips and 3 base Mult. Two plain Aces therefore score `(25+22)*3 = 141`. A level-1 Flush `AS,JS,9S,6S,2S` scores `(35+11+10+9+6+2)*4 = 292` without other effects; merely making a Flush does not guarantee clearing an Ante-1 Small Blind of 300.


## Sources

[^hands]: [Balatro Wiki: Poker Hands](https://balatrogame.fandom.com/wiki/Poker_Hands). Classification, scoring subsets and hand precedence.

[^planets]: [Balatro Wiki: Planet Cards](https://balatrogame.fandom.com/wiki/Planet_Cards). Planet mapping, level increments and secret hand eligibility.

[^patch]: [LocalThunk: Balatro 1.0.1f patch notes, May 1, 2024 (SteamDB mirror)](https://steamdb.info/patchnotes/14234225/). Developer-authored historical patch notes mirrored by SteamDB. Baseline changes, not proof of the latest installed build.

[^mods]: [Balatro Wiki: Card Modifiers](https://balatrogame.fandom.com/wiki/Card_Modifiers). Enhancement, edition and seal layers; debuffs.

[^four]: [Balatro Wiki: Four Fingers](https://balatrogame.fandom.com/wiki/Four_Fingers). Four-card requirements and overlapping-subset Straight Flushes.

[^shortcut]: [Balatro Wiki: Shortcut](https://balatrogame.fandom.com/wiki/Shortcut). One-rank gaps, with no wrapping around an Ace.
