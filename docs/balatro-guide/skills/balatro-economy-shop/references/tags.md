# Tags and skip timing

Read the visible tag, target and resolution state. Tags may resolve immediately or wait for a later event.

A skipped Small/Big Blind does not grant its played-round reward, unused-hand payout, interest, end-of-round effects or following shop. A tag may separately grant money or a pack; “skip pays $0” does not describe every resulting balance. Skip events may also affect specific Jokers/tags. Skipping both ordinary Blinds avoids playing cards there but does not authorize assumptions about all other card history.

| Tag | Effect | Timing |
| --- | --- | --- |
| Uncommon | Free Uncommon Joker | Next shop |
| Rare | Free Rare Joker | Next shop |
| Negative | Eligible base-edition shop Joker made Negative and free | Eligible next-shop offer |
| Foil | Eligible base-edition shop Joker made Foil and free | Eligible next-shop offer |
| Holographic | Eligible base-edition shop Joker made Holographic and free | Eligible next-shop offer |
| Polychrome | Eligible base-edition shop Joker made Polychrome and free | Eligible next-shop offer |
| Investment | $25 | After next Boss defeated |
| Voucher | Extra voucher | Next shop |
| Boss | Reroll Boss | On resolution |
| Standard | Mega Standard Pack | On resolution |
| Charm | Mega Arcana Pack | On resolution |
| Meteor | Mega Celestial Pack | On resolution |
| Buffoon | Mega Buffoon Pack | On resolution |
| Handy | $1 per hand played this run | On resolution |
| Garbage | $1 per unused discard counted this run | On resolution |
| Ethereal | Normal Spectral Pack | On resolution |
| Coupon | Initial shop cards/packs free, excluding vouchers | Next shop; not arbitrary rerolled stock |
| Double | Copy next eligible non-Double tag | Deferred |
| Juggle | +3 hand size | Next round |
| D6 | Starting reroll price $0 | Next shop |
| Top-up | Up to 2 Common Jokers, subject to room | On resolution |
| Speed | $5 per skipped Blind, including this skip | On resolution |
| Orbital | +3 levels to displayed hand | On resolution |
| Economy | Double money, gain capped at $40 | On resolution |

Multiple Double Tags add copies; do not model them as exponential doubling. A shop tag does not itself create immediate usable inventory or a new shop. Check the current queue, eligible offers and capacity as tags resolve; do not assume every queued effect can apply to the same offer.

Choose from the displayed tags. The guide does not specify exact minimum-Ante selection pools or future tag probabilities.

References: [Tags](https://balatrogame.fandom.com/wiki/Tags), [1.0.1f developer patch notes](https://steamdb.info/patchnotes/14234225/).
