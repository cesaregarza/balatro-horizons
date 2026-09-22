# Game interface

The game boundary is one typed session, not a second evaluator API. A caller
owns an `EvaluatorSession`; it starts a run, observes public state, submits one
enumerated action, and receives a settled observation or a typed rejection.

## Public information

The observation includes phase, ante/blind, money, score targets, consumables,
owned cards, visible offers, legal actions, recent public events, the previous
action's public changes, and a monotonic observation ID. Card rank/suit and
blind effects are exposed only when public. Concealed handles remain opaque;
encounter order, random salt, eviction, skip rewards, Stone Cards, and
`last_action` rules are part of the versioned information contract.
Handles use public encounter order and random episode salt, never a hash of
native card identity; concealment/disappearance evicts the mapping.
Blind `status` is `SELECT`, `CURRENT`, `UPCOMING`, `DEFEATED`, `SKIPPED`, or
`UNKNOWN`; `disabled` is true/false/null, with null meaning unknown.
New observations carry `schema_version: "1.1"`; model presentation renames it
`public_contract_version`, and `last_action.version` is `public_delta_v1`.
`owned_vouchers` and `pending_tags` are observed holdings, never inferred from
offered skip rewards. Stone Cards and replaced/absent rank or suit never expose
the underlying base identity; their deck-composition bucket is `No suit:No rank`.
`free_rerolls_remaining` is null when unobserved, never guessed from an effect.

## Actions and lifecycle

Actions cover selecting or skipping a blind, play, discard, reorder, buy, sell,
use, reroll, pack, leave, and cashout. Each request carries the current
observation ID. The session starts a fresh journal and request ledger, applies
the action, waits for settlement, and emits the next public observation. A
terminal state cannot accept another action; a new game gets a new identity.

`GameSession` is the normal contract. `EvaluatorSession` exposes calibration,
fixtures, replay, and raw inspection only to the evidence/operator boundary.
Neither interface exposes seeds, saves, arbitrary RPC, or endpoint text to an
agent. A retired or ambiguous native process is not silently retried.

## Errors and settlement

Invalid enumerated actions are structured public rejections and do not advance
the game. Lua infrastructure, transport, timeout, malformed response, and
unknown-status failures are private infrastructure failures and leave the
outcome unverified. Settlement requires the expected public transition and the
ready-frame policy; only the documented `ROUND_EVAL` event closes a round.
Absent interest is zero only for a complete, untruncated public breakdown;
interest hidden behind omitted rows remains unknown. The first seven public
settlement rows and native omitted-row count are preserved.

Native evidence is configuration-scoped: Red/White and Red/Gold are supported,
visible speed-1 execution is certified, and headless or accelerated modes are
not implied. Public economy fields distinguish transaction, balance, and cost;
the harness must not infer hidden values from the adapter.
`public-economy-v1` records committed cashouts/shop exits, sampled debt rounds,
and known score/target ratios. Unknown settlements are distinct from zero;
these descriptive metrics are not horizon judgments or spending targets.
