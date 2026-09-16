# Persistent playing-agent instructions

The current `tools_v5` harness always receives the short
[`ALWAYS-LOADED.md`](../configs/prompts/ALWAYS-LOADED.md) mechanics reference.
Its source is limited to 1,024 UTF-8 bytes. It supplies factual reminders and
instructions to consult uncertain rules and retain useful notes; it does not
prescribe a savings target or rank purchases.

Edit that Markdown file, then publish it into the current prompt:

```bash
/root/dev/balatro-horizons/.venv/bin/python /root/dev/balatro-horizons/scripts/sync_prompt_instructions.py --write
```

Without `--write`, the command checks freshness and fails if the embedded copy is
stale. The offline tests also check this. `tools-v5.txt` contains a generated block;
edit the Markdown source instead of that block. The update replaces the prompt
atomically so a worker always reads a complete file.

The existing episode-start protocol snapshot freezes the full prompt bytes and
their hash. Both provider adapters include those instructions on every call,
inside the stable prefix. Subsequent actions, helper continuations, and ordinary
branches retain the frozen prompt even if the source files change. New runs use
the updated prompt without a backend restart. Legacy prompt files are unchanged.
This is a recorded prompt change, not evidence of improved gameplay.

## Existing context-limit defect

`Limits.max_input_tokens_per_call` defaults to 32,768 in `config.py`. This setting
also determines the input-cost reservation. The runner passes it as `byte_limit`
to the context builder, which measures serialized UTF-8 JSON bytes plus 5,120
bytes of framing/settings allowance. The provider request has another check of
serialized bytes plus 4,096 against the same setting.

These are conservative byte-based token bounds, not the model's actual context
window or a tokenizer count. In a recorded Terra run the follow-up failed this
local check after a helper lookup, while the preceding request reported only
5,195 input tokens. Increasing the setting would also increase reservations.
Independent byte, token, and spending controls remain a separate fix; adding this
reference does not fix that defect and adds a small amount to the current bound.
Settlement/interest payout extraction is also still pending.
