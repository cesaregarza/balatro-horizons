# Run recovery and funding

These workbench-only controls require an operator token and `bh review --workbench`.
Preview is read-only: it contacts no game or provider. A passing preview establishes
admission readiness, not a claim that the upcoming replay has already succeeded.
Diagnose a timed-out request before retrying: timeout does not prove the worker
stopped or that an action was not committed.

## Restore unfinished runs

In **Decision Explorer → Restore run**, review the latest safe pre-decision
boundary, all-attempt spend, original episode/batch caps, remaining allowance and
source compatibility. Authorize paid execution, accept any compatible-code update
separately, then **Restore and continue**. Stale plans require renewed review.
Uncapped limits are red and require another “Are you sure?” confirmation.

GET `/api/operator/episodes/{eid}/restore` returns a plan or named refusal. POST
requires `parent_head`, `plan_hash`, strict `authorize_paid: true`, plus
`accept_compatible_update: true` and `confirm_uncapped: true` when applicable.
Confirmation switches the dashboard to live status.

The worker creates an unscored `assistance: restoration` child. Parent journals,
annotations, checkpoints and frozen protocols stay unchanged. One checked
seed-prefix replay reaches the boundary in the same process that continues;
there are zero preliminary verification launches. Public/private divergence or
unknown action status stops before a provider call. Model, prompt, knowledge,
memory policy and limits remain frozen. Post-boundary helper work is not inherited,
but every attempted call remains charged, including unknown-usage reservations.
Restore children share the root ledger and provider-call allowance; neither
retries nor code updates reset limits. Any remaining finite cap still applies.

Eligible parents are standalone failed, interrupted or unterminated runs and
their restoration children with a safe latest checkpoint. Wins and game losses
finish a run; a completed restore child closes the root family too. Campaign
members, evaluator fixtures, budget extensions/other intervention lineages,
unsettled actions, missing evidence, incomplete terminals, incompatible source,
exhausted caps and a sibling restoration in progress return named refusals.
There is no silent rewind or discarded future reuse. Increasing a dollar cap
uses the separate funding workflow below.

Older runs need the [source-compatibility proof](evidence.md#older-run-source-compatibility),
not rewritten hashes. A branch from an admitted compatible Restore child inherits
its proof; branching directly from an old-source root still refuses.

## Funding controls

For a **new model run**, choose **Current limits**, **$10 total** or **Uncapped**.
$10 sets both episode and standalone campaign ceilings to $10. The red Uncapped
choice asks “Are you sure?”; cancel preserves the previous selection. Choices
reset after successful launch or model change and never enable paid execution.
POST `/api/runs` accepts `cost_override: 10` or `cost_override: "uncapped"`, with
strict `confirm_uncapped: true` for the latter. Omit the override to keep defaults.

For a **cost-stopped standalone root**, select **Review cost override** in Decision
Explorer. **$10 more** means validated all-attempt spend plus $10, not $10 added
to the old cap. For example, $7 accounted makes the new ceiling $17. Retained
unknown-usage reservations count exactly once. The preview checks the actual
$10 cap and next-call reservation: `additional_available` is false with an
`additional_reason` when unusable. The $10 choice is then disabled; confirmed
Uncapped can remain available. Authorize the paid continuation and separately
accept a compatible-code update if requested.

GET `/api/operator/episodes/{eid}/continue-budget` returns the preview. POST
requires strict `authorize_paid: true` for **every** funding form, including a
numeric `combined_cap_usd`. The browser binds `parent_terminal_hash` and
`plan_hash`, then sends `additional_cost_usd: 10` or `combined_cap_usd: "uncapped"`
with `confirm_uncapped: true`. Numeric CLI/API total ceilings do not require a
preview hash. Global paid enablement is checked independently. Changed ledgers,
child heads or source invalidate a bound plan before child creation; refresh it.
An empty child journal returns `BUDGET_EXTENSION_CHILD_UNRESOLVED`, not a 500.

Saved settings, YAML defaults and batch planning/execution reject Uncapped.
Positive finite numeric caps must be at most $1,000,000; booleans, numeric strings,
nonfinite values and oversized integers refuse. Accepted integer caps serialize
as floats, preserving historical configuration hashes. Missing/null limits mean
unconfigured, never uncapped. Frozen private per-run Uncapped records remain valid.

Uncapped removes only the explicit dollar ceiling. Call, action, token and other
non-money limits remain. The remaining reservation envelope is **remaining
provider calls × configured maximum per-call reservation**, where that reservation
is `(max input tokens × maximum input price + max output tokens × output price)
/ 1,000,000`. All-attempt spend is retained separately. This is a harness funding
bound, not a provider invoice; it is not an unbounded allowance or new execution
authorization. Restore and **every branch mode**, including human takeover and
action override, reconfirm inherited Uncapped. Paid-model branches also require
the current global paid-enable setting, not just the parent's frozen permission.

### Provenance and accounting

A funded child has `assistance: budget_extension` and is never evaluation-eligible.
The root remains budget-exhausted; a later win is not an autonomous win at its old
cap. Model, prompt, tools, non-money limits, source identity and pre-decision
memory boundary stay frozen. Post-boundary helpers are charged without inheriting
their later notes/results. A code update requires exact-source compatibility and
explicit acceptance. The supported upgrade starts at deployed 16× `8807caf`;
it does not migrate the old 1× native runtime or certify historical checkpoints.

The combined cap includes root and every continuation attempt. Admission reconciles
one locked ledger snapshot with root/child terminals and admission hashes, not
SQLite ordering. Unknown-usage reservations remain charged when their terminals
account for them. Zero-spend children do not change the ledger hash. Lost rows
refuse, but this is not tamper-proof if both child rows and its index are deleted.
Run `bh recover` after index loss. A crash between `spending.settle` and the
`provider_response` journal write can leave a permanent
`BUDGET_EXTENSION_CHILD_SPEND_MISMATCH`; index recovery cannot reconstruct missing
response attribution. Each $10 admission independently checks reconciled cost + $10.

Exported metadata contains recovery policy, cap, source identities, admission
ledger hash, `root_batch_cap_usd` (old campaign ceiling) and `previous_cap_usd`
(old episode ceiling). Provider-call totals include inherited calls; child terminal
cost is own-attempt only. Funding targets the standalone root, never a batch member
or intervention child, and replay starts from that root's saved boundary.

### CLI

Use the owning checkout and an idle backend with the workbench enabled:

```bash
bh continue-budget EPISODE_ID --plan
# Optional diagnostic: three separately authorized unpaid launches, never required.
bh continue-budget EPISODE_ID --verify
# Explicit consent and total ceiling, not the browser's $10-more allowance.
bh continue-budget EPISODE_ID --start --combined-cap-usd 10 --authorize-paid
# Add --accept-compatible-update only after reviewing the compatible-code change.
```

The default plan contacts no game/provider; status reading records review exposure.
Starts and optional diagnostics use the operator-protected worker so the dashboard
can stop the child. Verification is not spending consent. Each start needs a cap
above the root's original binding cap (smaller finite episode/campaign ceiling),
not necessarily a prior child's cap, and enough remaining call reservation.
Action/call limits and batch slots are not extended. A passing initial-blind
fixture never overrides a historical direct-save replay failure or certifies
later checkpoints.
