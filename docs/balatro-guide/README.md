# Balatro Horizons Guide

**Version 1.0 · 2026-09-14**  
**Runtime target:** Balatro 1.0.1o-FULL with Steamodded 26.829.0.

The guide covers the rules and decisions needed to complete an ordinary native Balatro run. It separates mechanics references from strategy so the harness can retrieve the relevant material for each decision.

Start with the [decision router](skills/balatro-orchestrator/SKILL.md). Load one relevant skill, then its references as needed. Use current public state and active effects over handbook defaults. For unresolved interactions, consult [known limits](KNOWN_LIMITS.md).

The twelve modules cover core rules, scoring, plays/discards, Jokers, economy/shop, consumables, deck building, Boss Blinds, run strategy, decks/stakes, action validation and routing. Their relative links assume the folders remain together. [Harness tools](skills/balatro-orchestrator/references/harness.md) describes the supported interface.

The harness loads the generated `rules.json` for new guide-enabled runs and preserves a frozen copy for each episode and its branches. Start with `read_skill("balatro-orchestrator")`; `read_rules("guide")` or `read_rules("guide/index")` also opens the router. Chapter keys include `guide/balatro-scoring`, `guide/balatro-scoring/mechanics`, `guide/limits` and `guide/sources`. Compiled chapter links identify the corresponding lookup keys. Follow `next_key` with `read_rules` when a response has more text. Updating this bundle affects new runs; recorded episodes retain their original knowledge.

The objective is the Ante-8 native win. Plan across three horizons: **survive this Blind, survive this Ante, survive this run**. Use only public cards, effects and history. Hidden card identities, deck order, RNG and future outcomes remain unknown. A short decision note is optional when the tool schema supports it.

[Game references](SOURCES.md) provide further reading for rules and effects.
