# Model selection in the workbench

The run form selects an exact provider/model, reasoning effort, and harness independently. Configured aliases for the same provider/model appear once. Before defaults have been saved, the picker uses the newest already-configured harness for that model. The existing Terra cached configuration therefore supplies Terra's initial default; Luna retains its configured focused interface until cache prices are added.

Save model defaults persists the selection without starting a run. Starting a model run saves the selection first, then submits the resulting model configuration key. Both operations use the existing settings and run services. Settings are merged with a fresh server configuration to preserve spending authorization, limits, and skill selection. A failed settings save prevents launch. The server still enforces credentials, paid-execution authorization, budget ceilings, and worker isolation.

Defaults are stored under a deterministic provider/model key. Historical aliases remain available to existing manifests, branches, and frozen batches. Each episode already freezes the complete configuration in its immutable manifest. New batch plans select models with checkboxes and freeze their saved defaults. Existing plans are unchanged.

The harness dropdown represents actual supported interfaces: focused tools with prompt caching, focused tools, full-context tools, and the classic single-operation interface. All four use tool calls to submit moves; there is no misleading tools-off checkbox. The cached choice requires a recognized compatible OpenAI model and configured read/write rates. Switching harnesses preserves those pricing categories.

For GPT-5.6, the effort choices are none, low, medium, high, xhigh, and max. GPT-6 Astra offers low, medium, high, xhigh, and max (no none). Other model families retain their pinned settings instead of being assigned unverified effort options. Anthropic retains its configured thinking budget. [OpenAI's model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-terra) was checked on 2026-09-15.

Validation: TypeScript and production build; browser checks for alias deduplication, saved-default round trips, exact launch settings, stale-browser spending-limit preservation, batch planning, failed-save launch isolation, and mobile layout. Paid launch requests are intercepted. No paid calls or native launches are required. The existing decision explorer regression checks also pass.

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
