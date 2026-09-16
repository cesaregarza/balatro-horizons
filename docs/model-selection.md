# Model selection in the workbench

The run form selects an exact provider/model and reasoning effort. **Current harness (v5)** is the only selectable harness in both the run form and Models & budgets. It includes on-demand context, provider continuation within a decision, and prompt caching for supported OpenAI models; caching is not a separate competing harness choice. Configured aliases for the same provider/model appear once. Saved defaults supply the model's effort and pricing; legacy defaults are clearly labeled and new runs use v5.

Save model defaults persists the selection without starting a run. Starting a model run saves the selection first, then submits the resulting model configuration key. Both operations use the existing settings and run services. Settings are merged with a fresh server configuration to preserve spending authorization, limits, and skill selection. A failed settings save prevents launch. The server still enforces credentials, paid-execution authorization, budget ceilings, and worker isolation.

Defaults are stored under a deterministic provider/model key. Historical aliases remain available to existing manifests, branches, and frozen batches. Each episode freezes its configuration and agent protocol. New batch plans select models with checkboxes and freeze the current harness with their saved effort and pricing, including models whose previous default used a retired interface. Existing plans are unchanged.

`operate_v1` and `tools_v2` through `tools_v4` are no longer selectable in the app. Their backend support remains for historical configuration and continuation compatibility. The current harness requires a recognized compatible OpenAI model and configured read/write rates; a missing price blocks launch or batch creation rather than falling back to an obsolete interface. Anthropic retains its own provider settings without OpenAI cache requirements. All interfaces use tools to submit moves; there is no tools-off checkbox.

For GPT-5.6, the effort choices are none, low, medium, high, xhigh, and max. GPT-6 Astra offers low, medium, high, xhigh, and max (no none). Other model families retain their pinned settings instead of being assigned unverified effort options. Anthropic retains its configured thinking budget. [OpenAI's model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-terra) was checked on 2026-09-15.

Validation covers TypeScript and the production build; browser checks for alias deduplication, saved-default round trips, exact launch settings, stale-browser spending-limit preservation, batch planning, failed-save launch isolation, and mobile layout. The selector cleanup also checks legacy edit/upgrade behavior in both forms, batch upgrades, rejection of legacy harness values, and preservation of historical aliases. Launch requests are intercepted and the browser checks use an isolated test backend; no paid calls or native launches are involved.

The Playwright server command now puts `--data-dir` after `review`. The old order was overridden by the review subparser's default and unintentionally used the main data directory. Earlier test-created episodes remain labeled synthetic; temporary model settings were restored and compared with the live server. Subsequent verification uses `web/.e2e-data`.

Only web presentation, browser tests, and documentation changed. Native implementation fingerprints and certificates are unchanged. Deploy by building the frontend, copying hashed assets, then atomically replacing `web/dist/index.html`; an application or game restart is unnecessary.

## Sol and Astra registration (2026-09-15)

Registered `gpt-5.6-sol` and `gpt-6-astra` through `scripts/register_player.py`, cloning the saved Terra cached harness with medium effort and automatic reasoning summaries. Existing model configurations, skills, and budget limits were preserved. Registration starts no runs.

Standard short-context USD per million tokens, verified against [OpenAI pricing](https://developers.openai.com/api/docs/pricing):

| Model | Input | Cache read | Cache write | Output |
| --- | ---: | ---: | ---: | ---: |
| GPT-5.6 Sol | 4.00 | 0.40 | 5.00 | 20.00 |
| GPT-6 Astra | 10.00 | 1.00 | 12.50 | 50.00 |

The configured 32,768-token input ceiling remains below the long-context pricing threshold. Effort choices follow the official [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) and [Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) model documentation. Build and all four focused model-picker checks passed, including Astra's effort restrictions and both models' cached-harness defaults. Provider execution is untested for these two new entries; no paid validation was performed.
