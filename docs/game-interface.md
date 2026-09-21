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
Absent public interest is zero, not missing evidence. The first seven public
settlement rows and omitted-row behavior are tested invariants.

Native evidence is configuration-scoped: Red/White and Red/Gold are supported,
visible speed-1 execution is certified, and headless or accelerated modes are
not implied. Public economy fields distinguish transaction, balance, and cost;
the harness must not infer hidden values from the adapter.
