# Continuing after a cost cap

A cost cap is frozen into an episode. Changing launch settings cannot resume a
terminated run. An explicitly funded continuation creates an immutable child
with `assistance: budget_extension` and `evaluation_eligible: false`. The parent
stays budget-exhausted. A resulting win is an alternative continuation, not a
retroactive autonomous win under the original cap.

The first implementation supports standalone root episodes stopped by a dollar
cap, at their last recorded checkpoint. It does not extend action/call limits,
restart batch slots, or automatically roll funding into another continuation.
It preserves model settings, frozen prompts/tools, knowledge, public history,
identity mappings and pre-decision notes. A helper called after the checkpoint
is charged but its later note changes and tool results are not inherited.

The explicit combined dollar cap includes the original run and every continuation
attempt, including failures. The original spending ledger is shared; original
manifests, journals, protocol snapshots and checkpoints stay unchanged. Unsettled
reservations or mismatches between the journal and ledger prevent admission.
Source changes, the old/new caps, certificate and original protocol hash are
recorded in the child. Ordinary branches retain their strict source/budget checks.

Before resuming, the exact checkpoint must have a current restoration certificate.
For a stopped boundary without a recorded future, `checkpoint_probe` checks the
original private continuation hash in three fresh processes, applies the same
validated public action in each, and compares the resulting continuation hashes.
These generated probes are private evaluator evidence; they never enter the
parent journal or model context. This establishes bounded restoration evidence,
not the correctness of an unobserved original future. The first divergence stops
the checks and disables that boundary. No seed-replay fallback runs automatically.

Use the deployed checkout and an idle worker:

```sh
.venv/bin/python scripts/continue_budget.py EPISODE_ID --plan
# Only with Windows/runtime permission: three unpaid restoration launches.
.venv/bin/python scripts/continue_budget.py EPISODE_ID --verify
# Only with explicit combined spending authorization: one model-run launch.
.venv/bin/python scripts/continue_budget.py EPISODE_ID --start --combined-cap-usd 10
```

CLI starts go through the operator-protected web service, so the dashboard owns
and can stop the continuation. Verification and launch are separate commands;
successful unpaid checks do not authorize provider spending. A timed-out request
must be diagnosed before retrying. This path needs its targeted restoration checks,
not a repeat of the full native gameplay certification suite.
