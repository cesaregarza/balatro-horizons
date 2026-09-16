> Historical planning snapshot. The owner subsequently authorized implementation. Current behavior and evidence are documented in [the README](../README.md), [native audit](native-audit.md), and [verification report](verification.md). Earlier pause statements and defaults below are superseded.

# Native game to LLM: mod feasibility

Inspected 2026-09-14. Scope: source and documentation review only. No installation, native run, headless run, model call, or application implementation was performed in this assessment.

The owner confirmed that horizon analysis is the central purpose and requires complete native runs. An LLM must own the strategic decisions throughout each run. Headless operation is desirable, subject to preserving game behavior. The earlier instruction to pause the full application build remains in effect.

An existing mod supplies a credible starting point. The recommended architecture is a licensed native Balatro process with Lovely/Steamodded and an audited BalatroBot-compatible mod, connected to a small external LLM harness. The mod reports state and executes native actions; the harness supplies the player-visible context to the LLM, validates the returned action, and records the decision and resulting transition. Provider credentials stay in the harness.

| Candidate | Evidence inspected | Assessment |
| --- | --- | --- |
| `coder/balatrobot` | Resolved commit `e7c6db8a9ad88318f6e4128eefd6e61aafc94885`, committed 2026-06-17. Inspected endpoint registration and rendering settings at that revision, plus current API documentation and buy implementation. | Primary bridge candidate. Documentation covers blind selection/skip, play/discard, purchases, sales, consumables, pack actions, reordering, and shop transitions. Full native parity remains unverified. |
| `FFFishes7/blinddeck` | Resolved commit `8015c448d0a97e4f9d180ae6f1192412cc308871`, committed 2026-07-11. Inspected README and pinned boss-reroll implementation. | Relevant BalatroBot fork. README advertises boss reroll, hidden-card masking, and remaining pack choices. The native boss-reroll function call was confirmed; masking and complete action coverage were not audited. |
| `Attol8/balatro-ai` | Current README inspected; no revision pin or independent run validation in this assessment. | Additional example of an LLM harness using BalatroBot for native play. Its numerical assistance changes the evaluation condition, so it is background and a potential later assistance-track reference. |

The baseline endpoint registry contains shop reroll but no boss-reroll endpoint. Its buy schema provides card/voucher/pack indices without an explicit buy-and-use mode. These are concrete API coverage questions to resolve against actual native behavior. [Pinned endpoint registry](https://github.com/coder/balatrobot/blob/e7c6db8a9ad88318f6e4128eefd6e61aafc94885/balatrobot.lua), [buy implementation](https://github.com/coder/balatrobot/blob/e7c6db8a9ad88318f6e4128eefd6e61aafc94885/src/lua/endpoints/buy.lua), [API documentation](https://coder.github.io/balatrobot/latest/api/)

BlindDeck's boss-reroll endpoint invokes `G.FUNCS.reroll_boss(nil)`. It also requires the Boss to be the blind on deck, applies voucher/money/UI checks, and waits for the boss identity to change. Those additional restrictions and completion conditions require native parity tests; the presence of an endpoint alone is insufficient. The README also exposes a seed-query helper, so its agent-facing helpers cannot be adopted wholesale into the benchmark boundary. [Pinned boss-reroll source](https://github.com/FFFishes7/blinddeck/blob/8015c448d0a97e4f9d180ae6f1192412cc308871/src/lua/endpoints/reroll_boss.lua), [BlindDeck README](https://github.com/FFFishes7/blinddeck)

Headless support exists in the native bridge. At the inspected BalatroBot revision, it minimizes the game window, makes it 1×1, moves it off-screen, disables drawing/presentation, and changes the update timestep. Fast mode separately raises game speed and removes the FPS cap. Headless and render-on-API modes are treated as mutually exclusive. This supports unattended execution, but does not prove startup without a display service or equivalent game behavior under the altered timing. A virtual display may be needed on a Linux worker; that is an implementation possibility, not a tested host result. [Pinned settings implementation](https://github.com/coder/balatrobot/blob/e7c6db8a9ad88318f6e4128eefd6e61aafc94885/src/lua/settings.lua), [launcher documentation](https://coder.github.io/balatrobot/latest/cli/)

The intended decision loop is:

1. Wait for a verified native decision boundary and construct a player-equivalent observation.
2. Send that observation, permitted history, and bounded agent memory to the LLM.
3. Receive one explicit strategic action, validate it, and record its intent.
4. Execute the native action once; establish its result and wait for effects to settle.
5. Record the resulting public state and continue until a verified native win/loss or a separately classified harness/agent termination.

Blind play/skip, purchases, rerolls, use/sell, ordering, targeting, pack choices, and shop exit must remain agent decisions wherever the native game permits them. The benchmark gateway exposes neither raw seed/debug/save APIs nor automatic strategy. A later review interface can reconstruct which facts were visible when a choice was made and inspect how earlier choices shaped later situations.

The next bounded prototype, when authorized, should demonstrate one completely recorded native run through this bridge. Begin with a visible game for inspection, then replay committed actions in the intended headless environment and compare states and terminal results. A single successful trace is a plumbing demonstration; complete action coverage and checkpoint fidelity need their own cases. The existing offline scaffold is only test infrastructure and does not satisfy this native milestone.

Before choosing a fork or patch set, finish the action/visibility audit, establish the licensed game location and isolated profile, and pin the game/mod environment. No specific local game installation was confirmed by this assessment. Package-wide license/reuse review and exhaustive native validation remain outstanding. Full application implementation remains paused.
