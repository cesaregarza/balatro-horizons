# Verification and remaining gates

This is a local implementation verification, not a model-performance study. The initial release verification made no paid provider calls; subsequent Luna API checks, including a funded autonomous native run, are recorded in the OpenAI follow-up below. The ordinary native calibration runs use a transparent heuristic on a private regression panel chosen for short tests; they are excluded from benchmark scores. The Luna smoke is also excluded. The native win fixture changes evaluator setup and proves terminal detection, not autonomous playing strength. The persisted verification annotation tests storage and exposure provenance; it is not the owner's expert assessment.

## Focused context update — 2026-09-15

The then-current focused-context harness is preserved in the historical
[changelog](../CHANGELOG.md). It supplied a compact current board, bounded
retrieval results and paged access to public state, history and the frozen guide.
The matched offline comparison reduced median first-request size by 10.1%; all
106 recorded contexts and 2,544 additional guide-read reconstructions fit.

**124 Python tests pass with no skips**, including all native evidence gates.
Ruff, the Node 22.12.0 browser build and three browser tests passed. The separate
live desktop and phone checks passed native review and branch comparison with no
browser errors; the phone check used the actual Tailscale HTTPS route.

Fresh Red/White (`22531efca65f4b0a9b8f820865db5f8c`) and Red/Gold
(`c2abd23d1ca24548844a926a55525993`) heuristic calibration runs each completed five
committed actions and a verified engine loss. Both read a skill and guide chapter.
The unchanged native action collection was reused for the same pinned environment;
the action fixture and new Gold run each passed three fresh-process seed replays
with no divergence. Direct restoration at Gold decision 0 passed three repetitions.
Branch `bd19815f688043129fe15dd9af1868e9` completed with `GAME_LOSS`, leaving its
Gold parent unchanged. These checks are excluded from autonomous model scores.

Ordinary Gold startup (`5d75781005e34e7197bc82697f880d02`) passed with calibration
hooks disabled. Doctor has no blockers for either Red/White or Red/Gold. Native
capabilities were published after public-export validation and match source
fingerprint `38cdbc19b7e4e963609d07c7e9b32425452145a59e19723ca2b73b6efdb07234`.

[Activation evidence](../reports/verification/focused-activation.json),
[native replay/branch evidence](../reports/verification/native-release.json),
[ordinary startup](../reports/verification/certified-startup-run.json),
[desktop browser evidence](../reports/verification/native-browser.json) and
[mobile browser evidence](../reports/verification/native-browser-mobile.json)
record this update. Both provider adapters passed mocked focused-context workflows;
live paid-provider validation of `tools_v3` remains unverified. No paid calls were
made during this update. Existing spending ceilings are unchanged and paid
execution remains disabled.

## Skill access update — 2026-09-14

The [skill-enabled harness](harness-skills.md) has passed native recertification.
New runs default to twelve discoverable skills and paginated, frozen guide reads.
OpenAI and Anthropic use the same tool definitions and retrieval policy.

**113 Python tests pass with no skips**, along with Ruff, the browser build
and the three previously completed browser checks. All eight native acceptance
checks that were pending after the skill update now pass. The offline context
audit passes all 106 recorded contexts and 2,544 additional guide-read probes;
48 return an explicit context-limit response. No paid API call was made.

Two ordinary native calibration runs, Red/White and Red/Gold, each read a skill
and chapter through rules operations before the unchanged heuristic baseline
played to a verified game loss. Their guide snapshots pass integrity checks.
These are pipeline tests excluded from model performance scores. Named provider
tool compatibility is separately tested with mocked providers; live paid-provider
skill use remains unverified.

The action-coverage fixture and the new Gold run each passed three fresh-process
seed replays with no divergence. Direct restoration at Gold decision 0 also
passed three repetitions. Branch `9dbf647bed7a438f900cd279e9667015` completed with outcome
`GAME_LOSS`, preserving the parent journal and its frozen guide.
Normal native startup, without the calibration bypass, passed for both supported
configurations. [Startup evidence](../reports/verification/native-certified-start.json)
and [doctor output](../reports/verification/doctor-skills.json) record the checks.

Public-export checks now distinguish public URL schemes from Windows drive paths
while still rejecting private paths, credentials and known seeds, including those
inside URLs. All guide chapters pass the scan. The release script publishes the
active certificate after export validation; an injected export failure left the
active certificate, immutable records and evidence cache unchanged.

The current native evidence is in `reports/verification/native-release.json` and
`native-evidence.json`. Earlier verification details below describe the broader
coverage and its limits.

## Environment and artifacts

The dedicated Windows runtime uses Balatro **1.0.1o-FULL**, LÖVE **11.5.0**, Lovely **0.9.0**, BalatroBot **1.5.2** at `e7c6db8a9ad88318f6e4128eefd6e61aafc94885`, and Steamodded **26.829.0** at `39182f0cc7b1af86d3d3d6afc5422661a07b4312`. The Linux environment uses Python **3.12.10**, Node **22.12.0**, and the exact dependencies in `uv.lock` and `web/package-lock.json`.

Current execution/restoration source fingerprint: `38cdbc19b7e4e963609d07c7e9b32425452145a59e19723ca2b73b6efdb07234` (re-certified after focused context). The preceding skill-enabled native-certified revision was `d2ebdb108b4a3ac230ed65c27665db89192e356f81e52f5f85ab7ea1c8cfcad4`.

Native environment fingerprint: `36379d50272696e3b412c342854d4bd2e7ec86677107e57cc5ccda7112d100e0`. The private lock additionally pins the licensed executable, injector, bridge, and full mod tree. The initialized profile fingerprint is `73225727ee898255fc33416e87d6d06b5604a166fef5dfcee3e9014d3597082a` for both Red/White and Red/Gold. Startup checks the runtime identity, process nonce, loaded instrumentation, and frozen profile; stale capabilities cannot authorize a native episode.

`reports/verification/native-release.json` records the completed native collection, immutable parent/branch result, and direct-save check. `native-evidence.json` is the acceptance-gate cache derived by `scripts/finalize_evidence.py`, not a replacement for the actual episode journals or replay passes. `native-diagnostic-report.html` is a concise local diagnostic, and `public-native-diagnostic.json` is a schema-selected, scanned public export. Seeds, raw states, native saves, and divergence details remain in `data/private_runs/` and `private/`.

## Acceptance coverage

The tests are intentionally a bounded regression suite. An action-family pass does not establish every possible card, boss, mod, or stochastic combination. Unsupported native availability fails closed as an invalid evaluation rather than silently choosing a different move.

| Contract | Verification implemented | Evidence / practical limit |
| --- | --- | --- |
| AT-01 | Strict observation allowlists and injected private sentinels | `test_boundary.py`; public export scanner; no evaluator RPC in the playing policy |
| AT-02 | Complete public projections and legal constraints compared under hidden-state perturbations | Hidden identities, order, counters and consumable capabilities; native concealed-Joker fixture |
| AT-03 | Encounter-issued handles evicted on concealment/disappearance | Hidden shuffle/reveal cannot reconnect private IDs; native fixture plus projection tests |
| AT-04 | Dynamic resources, counters, prices, capacity and ordering | Gold action fixture, negative money with credit; large-number string round-trip is an offline check |
| AT-05 | All 14 strategic action families exercised natively | Blind skip, boss reroll, buy-and-use, non-shop targeted consumables, Mega pack multiple choices and skip rewards |
| AT-06 | Committed actions require recorded actor intent | Runner assertions and native intent/commit journals; no substituted moves |
| AT-07 | Malformed/stale/duplicate/phase/affordability checks and no-mutation rejection | Offline validation plus actual disabled Ankh and invalid Aura-target native tests |
| AT-08 | Explicit permutations retained through actions and certified replay | Hand/Joker order and target mapping in the Gold action fixture |
| AT-09 | Acknowledgment lost after actual native application | Known commit recovered once; duplicate had no effect; unknown status ends infrastructure failure |
| AT-10 | Fresh-process continuation checks with private/public comparisons | Three repetitions per certificate; seed-prefix fallback; direct saves enabled only where individually verified |
| AT-11 | Same-action seed replay through native boundaries | Three complete seed replays; selected per-decision certificates tied to source/environment |
| AT-12 | One native terminal result; Ante-8 win distinguished from loss | Ordinary-mechanics losses and an explicitly altered native win fixture |
| AT-13 | Torn-tail retention, ambiguous intent failure, index rebuild and cost recovery | Deterministic crash-persistence tests; native transport loss separately tested |
| AT-14 | Persistent cost reservation and request/helper/action ceilings | Live Luna request reservation and usage settlement passed; historical unknown usage retains reservation; costs are estimates, not invoices |
| AT-15 | Canonical provider parity, fixed direct HTTP hosts, response/schema validation | Both providers mocked; real OpenAI Luna completed a native smoke, Anthropic live compatibility remains untested |
| AT-16 | Server-owned progressive review cursor and recorded exposure | API tests with future/length/outcome withholding; browser reveal sequence |
| AT-17 | Interval annotations and append-only revisions | Confidence, alternatives, evidence and prior exposure retained; browser storage check |
| AT-18 | Immutable prefix and parent, explicit assistance | Native override continuation plus synthetic resume/sequence/takeover integration tests |
| AT-19 | Planned, valid, missing, first-valid attempts and all-attempt costs | Mixed-status, zero-valid and partially completed synthetic accounting cases |
| AT-20 | Seed-clustered uncertainty and seed/replicate matching | Deterministic bootstrap tests and both-missing pair regression |
| AT-21 | Schema-selected exports and escaped hostile text | Public scanner, authenticated download, browser injection test; native diagnostic export separately scanned |
| AT-22 | Synthetic provenance throughout | Offline runner, browser, reports and fixture labels; synthetic checks do not grant native capability |
| AT-23 | Separate identity, frozen initialization and pinned runtime | Two fresh processes per supported stake; per-profile save inputs blocked; no personal profile inheritance |
| AT-24 | Uncertified environment/decision and assisted-score rejection | Native startup and branch gates; private evaluator capability outside the public operation schema |

## Certification scope

A seed-prefix certificate certifies only the recorded decisions and continuations it actually replayed. It is not universal save support. The action fixture covers blind selection, round evaluation, shop, and multi-choice booster phases; the ordinary Gold run supplies selecting-hand continuation evidence. Rechecks preserve immutable certificate records and a selected current certificate. A failed check disables that restoration mode; a separately passing mode can remain available. Private divergence artifacts identify the first mismatching boundary.

Execution/restoration source is fingerprinted separately from presentation, API and report-only code. A source edit during a replay invalidates that check. Historical interrupted or source-change checks remain visible and do not become passing evidence. Native mechanics, environment pins, observation projection, action execution, storage, runner, policy and restoration edits require fresh matching certificates.

## Remaining operator gates

- **Paid models:** supply explicit OpenAI/Anthropic model IDs, token prices/date, supported provider settings, backend environment credentials, and episode/batch ceilings. Enable paid calls in Models & budgets. Installation and the exploratory preset authorize no spending. Provider usage costs are estimates from configured prices, not invoices.
- **Headless and acceleration:** disabled. Visible speed-1 execution is the only tested runtime mode; no equivalence claim is made.
- **New decisions/configurations:** verify the recorded decision before branching. Additional decks, stakes, unusual card/boss interactions, and modified mechanics require their own coverage; existing evidence is bounded to Red/White and Red/Gold in the pinned environment.
- **Scientific use:** freeze a development panel and model settings before running; retain separate held-out seeds. Regression fixtures, implementation annotations and assisted continuations are diagnostic evidence and cannot establish an optimal move, a causal share of failure, or a model win rate.

## Reproduction

Run the following from the Linux repository using its locked environment:

```bash
uv run pytest -q --junitxml=reports/verification/pytest.xml
uv run ruff check src tests scripts
npm --prefix web run build
npm --prefix web test
# Serial native checks; no paid requests:
uv run python scripts/verify_release.py
uv run python scripts/finalize_evidence.py
uv run bh doctor --json
```

After activation, `scripts/verify_gated_run.py` exercises certified startup with calibration hooks disabled, while keeping the reused regression seed excluded from scores. With the workbench on port 8765, `node scripts/verify_browser_native.mjs --release` verifies the actual native review and branch comparison in Chromium.

The browser tests use a separate synthetic data directory. Native verification uses the dedicated Windows runtime, not the personal game installation. The completed counts and native episode references follow.


## Initial release checks — 2026-09-14

- **Python:** 55 passed, 0 failed, 0 skipped at initial release. The latest `reports/verification/pytest.xml` records 94 passing checks after the named-tools follow-up. Two test-client deprecation warnings remain.
- **Browser:** 2 Playwright tests passed, including synthetic review, saved annotation, override creation/comparison and mobile layout. The separate actual-native browser check passed with zero page errors (`native-browser.json`).
- **Build/style:** `npm run build` (TypeScript + Vite) and `ruff check src tests scripts` passed.
- **Native release:** `verify_release.py --resume-certification` passed against the unchanged pinned runtime. Its earlier collection ran both fresh-profile audits, the Gold action and win fixtures, native rejection and timeout tests, and ordinary Red/White and Red/Gold calibration runs. Resuming repeated the full replay proofs and branch test; it did not infer certification from old certificates.
- **Capability activation:** `finalize_evidence.py` passed; `bh doctor --json` passed for both smoke and pilot configurations with no blockers.
- **Ordinary startup:** `verify_gated_run.py` passed with calibration hooks disabled and a verified native terminal result. It deliberately remains excluded from performance estimates because it reuses a regression seed.
- **Paid calls/cost at initial release:** 0 calls, $0.00. Subsequent funded Luna checks are recorded below; Anthropic retains mocked transport/parity coverage only.

| Native artifact | Episode | Result |
| --- | --- | --- |
| Ordinary Red/White calibration | `4807da71d69c445d87f311d8aa3eca92` | GAME_LOSS, 5 committed actions |
| Ordinary Red/Gold calibration and branch parent | `aff5171f2e2d4e579e34ae02b9b787f7` | GAME_LOSS, 5 committed actions |
| Alternative native continuation | `b7ee4053a4b2488e8b757e599deb937a` | GAME_LOSS, 6 committed actions; original journal unchanged |
| Certified ordinary startup, calibration hooks off | `b6f87583b61546cf8b612e6d6e653535` | GAME_LOSS, 5 committed actions |
| Gold strategic-action fixture | `a037f707d1354a81be49cfc7e7c2ec3a` | 14 action families covered; 23 explicit strategic operations |
| Native Ante-8 terminal fixture | `e97d613eb6d04a5ca6974dcbc7352324` | WIN under altered evaluator setup; not an autonomous win |

Current seed-prefix certificates cover BLIND_SELECT, ROUND_EVAL, SHOP, SMODS_BOOSTER_OPENED and SELECTING_HAND at the tested boundaries. Each proof used three fresh native processes. Direct-save restoration separately passed three repetitions at decision 0 of the Gold parent; no other direct-save phase is claimed.

The native annotation (`078f27a43a2a439a8e4883f95dbd7183`) is explicitly labeled implementation verification and records prior outcome exposure. No expert horizon labels or model-performance conclusions have been invented. Both original and alternative calibration runs lost; the branch establishes a working alternative continuation, not a successful rescue.

Read the generated [native diagnostic report](../reports/verification/native-diagnostic-report.html), [sanitized native trace](../reports/verification/public-native-diagnostic.json), [native review screenshot](../reports/verification/native-review.png), and [branch comparison screenshot](../reports/verification/native-comparison.png). Synthetic browser fixtures live separately under `web/.e2e-data/`; pytest fixtures use temporary directories. Full native journals live under `data/`, with private evidence separated as described above.

## OpenAI harness follow-up (2026-09-14)

The [current harness](harness.md#providers) has a pinned Luna preset, optional returned
reasoning summaries, standard-tier requests with caching disabled for Luna,
explicit long-context pricing rejection, incomplete-response rejection, and safe
quota diagnostics with bounded retries. The browser reveals summaries and provider
errors at the existing action stage. Its existing Tailscale URL is unchanged.

The original OpenAI follow-up passed **82 tests with no skips**, including the refreshed
native-evidence gates. The browser build passed using
Node 22.12.0; both Playwright desktop/mobile tests passed.

After the account was funded, the real Responses API completed a one-action
synthetic transport smoke and an autonomous native Red/White run. Native episode
`c0ed932043be429d972806833b4bb145` reached **GAME_LOSS on Ante 2** through a
verified engine terminal: **50 committed actions, 54 completed API responses,
$0.0831404 estimated usage cost**, and no provider errors. Forty-one responses
contained returned reasoning summaries. This diagnostic episode is excluded from
benchmark scores; no model-performance conclusion follows from it.

The synthetic check cost $0.000357, making settled estimates **$0.0834974** across
both funded checks. Seven earlier credit-exhausted requests retain **$0.114688**
in conservative unknown-usage reservations. Campaign accounting is therefore
**$0.1981854 of the authorized $5**, with a $1 episode ceiling. These reservations
are not confirmed provider charges. The credit blocker is resolved; historical
failed attempts remain recorded.

The live native journal's hash chain and schema-selected public export passed
verification. All decision boundaries passed progressive-review checks using the
actual returned responses; prior monitoring/export exposure is retained. See
`reports/verification/openai-harness.json` and the immutable per-episode smoke
report for details. Branch certification for this new episode has not been run.

Harness changes invalidated the earlier source fingerprint. Initial refresh
attempts encountered a Windows bridge I/O error and interrupted startups; their
failures remain preserved. After dedicated-runtime cleanup and freezing the final
source, the complete native action fixture and ordinary Gold seed replay each
passed three fresh-process repetitions. Direct-save restoration also passed three
repetitions. The new branch `fe9fa801ca954d91a04c05f80de1fb0e` completed
with its parent unchanged. Matching native capabilities are active again.
Anthropic live execution, new-decision restoration, and headless equivalence
remain separate gates.

## Named-tools harness follow-up

The historical named-tools interface added game tools, on-demand public
inspection, a smaller automatic context, and structured rejection feedback. Its
complete prompt is preserved in the [changelog](../CHANGELOG.md); the interface
itself is retired.

Native verification was refreshed after freezing the new source. The complete
action fixture passed three seed replays (certificate
`b6ca163fe7ac4ec4aad70a0a49c28aef`), and the ordinary Gold run passed three
seed replays (`606cad74e49c4a6aba15d3bbd5988440`). Direct restoration at that
run's decision 0 passed three repetitions (`1faf021a553641248ed93ab7d07aa184`).
Branch `b9182c31369f4120b5693163d7d36247` completed with its parent unchanged.
Matching capabilities are active and `bh doctor` passes with no blockers.

All **94 Python tests passed with no skips**. The browser build and both desktop/mobile tests passed. The new preset also
passed one real Luna API call against the synthetic transport fixture. This
establishes basic tool-schema compatibility; native model execution is recorded
separately from that fixture. Headless equivalence and Anthropic live execution
remain untested.

The initial named-tools native trial committed 98 actions and used six inspection
calls before a duplicated-context overflow ended it in the Ante 4 shop. Its
`INFRASTRUCTURE_FAILURE` outcome remains unchanged. Inspection coalescing and ID
constraints were then implemented and the source re-certified as listed above.
All 106 recorded decision contexts now reconstruct within the existing request
ceiling, including the failed one. The public export passes its scan. A real-API
replay of that corrected request awaits the explicit approval requested after
automatic approval review rejected the recorded-data replay. No replay request
was sent, and no corrected full native model run is claimed.
