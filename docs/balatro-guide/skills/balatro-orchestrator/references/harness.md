# Balatro Horizons tools

The `tools_v2` interface offers named tools for the current phase. Use their current schemas, observation ID, visible object IDs and selection limits. Submit one game action, then use the returned settled state before making a dependent choice.

| Tool | Purpose | Scope |
| --- | --- | --- |
| `inspect_state` | Retrieve more current public details | Masked state sections only; no hidden identities, seed, RNG or future state |
| `read_history` | Retrieve earlier public events | Bounded pagination of public history |
| `read_skill(name)` | Read one available Balatro skill | Names come from the supplied catalog; follow `next_key` with `read_rules` for another page |
| `read_rules(key)` | Read a frozen rule entry | `index` lists keys; exact keys and registered aliases resolve entries |
| `calculate(expression)` | Evaluate arithmetic | Numeric literals, unary signs, addition, subtraction, multiplication, division and modulo |
| Phase-specific actions, including `play_hand`, `discard`, `buy`, `select_blind` | Advance the game | Only actions and targets offered by the current phase are available |

Rules and skill lookup read the run's frozen dictionary. In guide-enabled runs, `guide` and `guide/index` open the router; a module uses a key such as `guide/balatro-scoring`, and its mechanics reference uses `guide/balatro-scoring/mechanics`. `guide/limits` and `guide/sources` open the shared references. Use `index` to discover the keys currently available. Lookup does not search arbitrary files or web pages. A rule entry's descriptive text may not specify every timing interaction. Arithmetic is not hand recognition or a scoring simulation; function calls, exponentiation and combinations are not accepted by `calculate`. Translate a supported expression into the actual arithmetic syntax rather than sending mathematical notation unchanged.

A hand-type display and base Chips/Mult are not a full score preview. Estimate scoring from the public cards, effects, order and counters, and label uncertainty when a needed hook is unknown.

Physical card/Joker order, selection order and the order of IDs in a note can differ. Use the supported reorder semantics and re-observe before a dependent play or Death use. Duplicate cards have distinct IDs; an opaque ID does not reveal a concealed card. Null or absent fields mean unknown, not zero or a default.

Optional decision notes and bounded memory belong only in fields supported by the active tool schema. A concise note can state the decisive tradeoff or uncertainty; a step-by-step reasoning transcript is not required. Revalidate remembered plans against each new state.

Helpers leave the game unchanged and remain subject to the current decision's call and context limits. A rejected action does not authorize changing its targets silently. If execution status is unresolved after a timeout, Horizons ends the episode as an infrastructure failure; the player must not retry a possibly executed action.

The initial context supplies skill names and short descriptions. Read only the needed skill or reference. Pages carry `complete` and `next_key`; follow the latter through `read_rules` when needed. A `REFERENCE_CONTEXT_LIMIT` result means no additional page fits this decision. Existing context is retained. Skill text lasts for the current decision; save concise reminders or chapter keys in explicit memory if needed after the next action. The older `operate_v1` interface uses a `rules` operation with the same `guide/...` keys.
