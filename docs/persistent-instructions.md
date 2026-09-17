# Persistent playing-agent instructions

The current `tools_v5` harness always receives the short
[`ALWAYS-LOADED.md`](../configs/prompts/ALWAYS-LOADED.md) mechanics reference.
Its source is limited to 1,024 UTF-8 bytes. It supplies factual reminders about
interest, unused hands, borrowing, scoring, persistent resources, and skipping;
it does not prescribe a savings target or rank purchases.

Edit that Markdown file, then publish it into the current prompt:

```bash
/root/dev/balatro-horizons/.venv/bin/python /root/dev/balatro-horizons/scripts/sync_prompt_instructions.py --write
```

Without `--write`, the command checks freshness and fails if the embedded copy is
stale. New `tools_v5` runs also check this before starting the game: stale content
raises `PERSISTENT_INSTRUCTIONS_STALE`, while missing or malformed source content
raises `PERSISTENT_INSTRUCTIONS_INVALID`. Both admission and the editing script
use the same renderer. `tools-v5.txt` contains a generated block;
edit the Markdown source instead of that block. The update replaces the prompt
atomically so a worker always reads a complete file.

The existing episode-start protocol snapshot freezes the full prompt bytes and
their hash. Both provider adapters include those instructions on every call,
inside the stable prefix. Subsequent actions, helper continuations, and ordinary
branches retain the frozen prompt even if the source files change. New runs use
the updated prompt without a backend restart. Legacy prompt files are unchanged.
This is a recorded prompt change, not evidence of improved gameplay.

## Separate transport, token and spending controls

`Limits.max_request_bytes` defaults to 262,144 in `config.py`. Context construction
uses this independent transport bound, including framing/settings headroom;
the final request is checked using the HTTP JSON encoding. This value does not
change token allowances or dollar reservations.

`max_input_tokens_per_call` remains 32,768. Before generation the adapter sends
the complete supported input fields, including tools and original provider items,
to the provider's counting endpoint. Counts receive a 512-token safety margin;
that margin is headroom, not a proof of exactness for every provider/model.
An unavailable or malformed count stops before generation. Identical input on
transport retries reuses its count. Counts do not warm the generation cache.
`provider_input_check` records the count, payload hash and separate bounds.

Reservations still use the full input allowance at the highest configured input
price, plus the output ceiling. Unknown generation usage retains that reservation;
actual reported costs are recorded. No ciphertext-byte/token conversion is used.
`LOCAL_CONTEXT_LIMIT`, `INPUT_TOKEN_LIMIT`, and `TOKEN_COUNT_UNAVAILABLE` identify
the specific local failure. Private diagnostics contain stack locations, without
exception messages, source lines or locals.

The previous coupling caused a recorded Terra helper follow-up to fail after a
5,195-token request. `scripts/replay_helper_context.py` reproduces the local bound
offline and verifies preservation of provider items. This is transport evidence,
not a live token count, native continuation, or new model result.

See [the reliability change record](harness-reliability.md) for settlement parity,
descriptive economy metrics and the required deployment gates.
