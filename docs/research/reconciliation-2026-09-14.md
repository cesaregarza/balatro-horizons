# Research return: decisions, proposals, and implementation audit

The [return handoff](return-handoff-2026-09-14.md) is preserved byte-for-byte. Its research session had no repository access, execution, paid calls, or literature search. This reconciliation inspected the local code and official provider documentation. It changes documentation only; it does not select a study design, migrate annotation data, or authorize execution.

## User direction carried forward

- Intended use: a dangerous-capability early-warning evaluation. The user wants to monitor a proposed prerequisite of scheming across model generations and classify present mistakes. The empirical relationship between the measured behavior and scheming remains a research question.
- Horizon definitions: immediate = survive this blind; short = survive this ante; long = survive this run.
- Central situation: the interaction of the revealed boss with the current build. Both threatening and favorable interactions matter; the latter can create an opportunity to invest.
- Performance intent: full-run completion, with highest stake completed as the proposed primary metric. Ante reached or percent completion is not the preferred performance metric. How repeated wins establish a rung remains undecided.
- Diagnostic emphasis: whether relevant knowledge is applied spontaneously to the current situation, including both under-application and over-application.
- The user validated the recognition → valuation → planning/adaptation taxonomy in the source handoff. The exact annotation implementation has not been selected.
- The user's skill ladder and Joker assessments are expert input to operationalize and check, not automatically established statistical facts.

Current runtime defaults remain Red/Gold and Red/White smoke. Changing research intent has not silently changed configurations, journals, existing labels, or benchmark outputs.

## Proposals remain proposals

The following have not been selected for implementation: a win-rate threshold staircase, prospective viability ratings, ante-entry windows as the primary annotation unit, interaction classes and explicit candidate-out enumeration, stage-signature extraction, cued/uncued configurations, constructed probes, cold/warm handovers, memory overrides, low-stake paid pilots, and publication/withholding policies.

The navigation complaint remains actionable design feedback: support returning to revealed decisions and inspecting intervals, while retaining prospective isolation. Making ante-entry windows a first-class unit is a further proposal. Define the timing before implementing it: expert observation at boss reveal, end-of-window review, and visibility of the first shop are different exposure conditions. Skips and changing shop access also need explicit window-boundary rules.

Existing annotation tags must not be retroactively relabeled with the new blind/ante/run definitions. A future rubric needs a version and migration/compatibility policy.

## Reasoning-capture audit

Historical audit at research intake. The subsequent [OpenAI harness work](../openai-harness.md) adds explicit summary requests and a returned-summary viewer; the limits concerning internal reasoning and explicit memory still apply.

Inspected source:

- `src/balatro_horizons/agents/providers.py`: request, send, parse, and canonical-message construction.
- `src/balatro_horizons/config.py`: allowed provider settings.
- `src/balatro_horizons/runner.py`: provider response logging before operation parsing.
- `src/balatro_horizons/contracts.py`: optional `decision_note` and `memory_update`.
- `src/balatro_horizons/review/service.py`: provider records become available at action reveal.

| Layer | Current implementation | Limit |
| --- | --- | --- |
| Transport logging | Saves the complete successfully decoded provider response JSON before parsing the requested operation | This is the returned API body, not access to hidden internal computations; no paid response has yet been collected |
| OpenAI request | Supports `reasoning_effort`, mapped to `reasoning.effort` | No `reasoning.summary` setting or explicit summary request is implemented |
| Anthropic request | Supports `thinking_budget`, mapped to manual `thinking.type=enabled` with `budget_tokens` | No adaptive-thinking or explicit thinking-display configuration is implemented; model compatibility needs checking |
| Operation parser | Extracts exactly one `operate` tool call | Other returned content stays in the journal but is not the action or a separate structured analysis |
| Human-readable decision report | Optional `decision_note` on an action | It can be absent and is a model-authored account |
| Across-call context | Rebuilds canonical public messages, helper exchanges, and explicit bounded memory | It does not replay provider-native thinking blocks or OpenAI reasoning items |
| Viewer | Includes provider response events after action reveal | No dedicated cross-provider reasoning-coverage representation exists |

OpenAI documents reasoning summaries as opt-in and distinguishes them from raw reasoning tokens. Therefore setting effort alone does not provide the proposed summary channel. [Official reasoning guide](https://developers.openai.com/api/docs/guides/reasoning).

Anthropic documents summarized thinking output and model-dependent support for the manual budget mode used here. A configuration supported by one model cannot be assumed compatible with another. [Official extended-thinking documentation](https://platform.claude.com/docs/en/build-with-claude/extended-thinking).

Consequently, the proposed coverage measure cannot currently be described as the fraction of all options the model internally considered. At most, it could measure options explicitly reported in a specified available channel against a fallible, versioned expert reference set. Missing reports are not proof of missing consideration. Provider/channel differences, omitted/redacted content, elicitation effects, and the memory policy need explicit treatment before comparing models.

## Claims to keep separate from findings

These are methodological cautions from this reconciliation, not new user decisions or a completed literature review:

1. A game without a scheming narrative may reduce one obvious cue, but does not establish absence of evaluation awareness.
2. A stake-rung failure does not by itself identify a horizon mechanism. Stakes accumulate restrictions and alter several demands; targeted comparisons are needed for localization.
3. Expert prospective ratings can be assessed for calibration, but do not alone distinguish variance from a reasoning failure or establish optimal play.
4. Improvement from cueing can reflect supplied information or additional reasoning effort as well as improved applicability recognition. Cue-content and budget controls matter.
5. The eleven taxonomy steps are useful categories, not mandatory observable internal steps. Allow multiple suspected failure points, uncertainty, and unclassifiable cases.
6. Absence of an expert-listed candidate from a verbal report is not evidence that it was absent from computation.
7. A better branch demonstrates an alternative continuation. It does not establish a uniquely correct tradeoff or assign a causal fraction of failure.
8. The dangerous-capability early-warning interpretation needs an explicit validation argument; the intended safety use alone does not validate the proxy.

## Suggested next concrete deliverable

Draft a versioned ante-entry review card around the user's Plant/Luchador example: the exact available information, boss–build interaction, severity, candidate outs, acquisition uncertainty, timing, irreversible costs, and uncertain taxonomy labels. Pair it with proposed navigation that can revisit revealed windows without disclosing future windows or episode length.

Keep this a reviewable draft until the user selects the annotation unit and measures. Confirm descriptive stage statistics before implementing them, as requested in the return handoff. No paid pilot, provider migration, automatic horizon score, training procedure, or publication was started during reconciliation.
