# Model selection in the workbench

New runs use one current harness in the run form and Models & budgets. It provides on-demand context, provider continuation within a decision, prompt caching for supported OpenAI models, cost accounting, keyed run notes, direct action-result retrieval, and bounded helper exhaustion. These capabilities support notebook-style runs without changing the exact provider/model and reasoning-effort selection. Configured aliases for the same provider/model appear once. Saved defaults supply the model's effort and pricing; historical recorded runs remain viewable.

Save model defaults persists the selection without starting a run. Starting a model run saves the selection first, then submits the resulting model configuration key. Both operations use the existing settings and run services. Settings are merged with a fresh server configuration to preserve spending authorization, limits, and skill selection. A failed settings save prevents launch. The server still enforces credentials, paid-execution authorization, budget ceilings, and worker isolation.

Defaults are stored under a deterministic provider/model key. Historical aliases remain available to existing manifests, branches, and frozen batches, while an explicitly saved canonical provider/model key wins. Each episode freezes its configuration and agent protocol. New batch plans select models with checkboxes and freeze the current harness with their saved effort and pricing. Existing plans are unchanged.

Retired interface prompts and configurations are archived in the
[changelog](../CHANGELOG.md), not selectable or accepted for new runs. The current
harness requires a recognized compatible OpenAI model and configured read/write
rates; a missing price blocks launch or batch creation. Anthropic retains its own
provider settings without OpenAI cache requirements. The harness uses tools to
submit moves; there is no tools-off checkbox.

The selector and backend were deployed together on September 17 after the offline
checks and one consolidated native certification suite passed. Existing model
settings and spending limits were preserved. The historical deployment record is
summarized in the [changelog](../CHANGELOG.md).

For GPT-5.6, the effort choices are none, low, medium, high, xhigh, and max. GPT-6 Astra offers low, medium, high, xhigh, and max (no none). Other model families retain their pinned settings instead of being assigned unverified effort options. Anthropic retains its configured thinking budget. [OpenAI's model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-terra) was checked on 2026-09-15.

Validation covers TypeScript and the production build; browser checks for alias deduplication, saved-default round trips, exact launch settings, stale-browser spending-limit preservation, batch planning, failed-save launch isolation, and mobile layout. The selector cleanup also checks legacy edit/upgrade behavior in both forms, batch upgrades, rejection of legacy harness values, and preservation of historical aliases. Launch requests are intercepted and the browser checks use an isolated test backend; no paid calls or native launches are involved.

The Playwright server command now puts `--data-dir` after `review`. The old order was overridden by the review subparser's default and unintentionally used the main data directory. Earlier test-created episodes remain labeled synthetic; temporary model settings were restored and compared with the live server. Subsequent verification uses `web/.e2e-data`.

The earlier selector cleanup changed only presentation and could deploy through
static assets. Executable harness changes also alter the native
implementation fingerprint. It requires matching native certification and an idle
worker before the backend and frontend are deployed together; copying only the
new selector would expose unsupported behavior on the old backend.

## Sol and Astra registration (2026-09-15)

Registered `gpt-5.6-sol` and `gpt-6-astra` through `scripts/register_player.py`, cloning the saved Terra cached harness with medium effort and automatic reasoning summaries. Existing model configurations, skills, and budget limits were preserved. Registration starts no runs.

Standard short-context USD per million tokens, verified against [OpenAI pricing](https://developers.openai.com/api/docs/pricing):

| Model | Input | Cache read | Cache write | Output |
| --- | ---: | ---: | ---: | ---: |
| GPT-5.6 Sol | 4.00 | 0.40 | 5.00 | 20.00 |
| GPT-6 Astra | 10.00 | 1.00 | 12.50 | 50.00 |

The configured 32,768-token input ceiling remains below the long-context pricing threshold. Effort choices follow the official [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) and [Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) model documentation. Build and all four focused model-picker checks passed, including Astra's effort restrictions and both models' cached-harness defaults. Provider execution is untested for these two new entries; no paid validation was performed.
