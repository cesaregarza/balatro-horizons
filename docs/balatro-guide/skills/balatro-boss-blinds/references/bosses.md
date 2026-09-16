# Boss effect catalog

Use the observed Boss effect, target and active/disabled state. For counterplay, read the [Boss decision skill](../SKILL.md).

| Boss | Baseline effect |
| --- | --- |
| The Hook | After play, 2 random held cards discarded |
| The Ox | Playing the named hand sets cash to $0 |
| The House | Initial hand face down |
| The Wall | Target ×4 base instead of usual ×2 |
| The Wheel | Drawn cards have 1-in-7 face-down chance |
| The Arm | Played hand loses one level before scoring, minimum level 1 |
| The Club | Clubs debuffed |
| The Fish | Cards drawn after a play face down |
| The Psychic | Requires five played cards; five scoring cards not required |
| The Goad | Spades debuffed |
| The Water | Removes starting discards |
| The Window | Diamonds debuffed |
| The Manacle | −1 hand size |
| The Eye | A hand type cannot repeat within this round |
| The Mouth | First played hand type constrains later scoring hands |
| The Plant | Face cards debuffed |
| The Serpent | Three-card refill after play/discard instead of ordinary hand-size refill |
| The Pillar | Cards played earlier this Ante debuffed |
| The Needle | Normally one hand; target ×1 base |
| The Head | Hearts debuffed |
| The Tooth | Lose $1 per played card; debt possible |
| The Flint | Hand base Chips and Mult halved |
| The Mark | Face cards drawn face down |
| Amber Acorn | Jokers concealed and shuffled; effects persist |
| Verdant Leaf | Playing cards debuffed until Joker sale |
| Violet Vessel | Target ×6 base |
| Crimson Heart | One Joker disabled; changes after hands |
| Cerulean Bell | One held card forced selected for actions |

Normal Boss multiplier is ×2. Showdowns occur every eighth Ante. Do not infer the next Boss from a presumed repetition cycle. Cerulean reselection timing, Serpent capacity edges and Flint rounding are covered in [known limits](../../../KNOWN_LIMITS.md).

A debuffed card can still classify a hand but normally loses scoring, held and discard effects; “triggers nothing” is too broad for every whole-hand or played-set check. Wild's active all-suit membership exposes it to all suit Bosses; while debuffed its all-suit behavior is removed. Stone's rank/suit/debuff edge cases require actual build evidence.

Face-down means unknown, not inactive. A discard does not universally guarantee a visible replacement: Wheel/Mark/Fish/House or other active effects can change visibility. Retaining prior identity requires publicly valid continuity; an opaque ID is not hidden rank/suit data. Boss effects normally end with the round, but their consequences—Arm levels, Ox/Tooth money, for example—can persist.

| Counter mechanism | Effect and timing |
| --- | --- |
| Director's Cut | $10 Boss reroll once per Ante, when legal |
| Retcon | Additional $10 Boss rerolls, when legal |
| Boss Tag | Boss reroll on tag resolution |
| Luchador | Sell during the active Boss to disable its effect |
| Chicot | Disables Boss effects |

After a Boss reroll or disable, inspect the actual effect and target. Target changes for Wall, Violet Vessel and Needle are not specified exactly here. Start-of-Blind resource effects such as Burglar can alter Needle's normal one-hand result; use the resulting counters.

References: [Blinds and Antes](https://balatrogame.fandom.com/wiki/Blinds_and_Antes), [Burglar](https://balatrogame.fandom.com/wiki/Burglar), [Jokers](https://balatrogame.fandom.com/wiki/Jokers), [Vouchers](https://balatrogame.fandom.com/wiki/Vouchers).
