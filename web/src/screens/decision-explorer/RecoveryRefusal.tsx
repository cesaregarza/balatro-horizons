const messages: Record<string, [string, string]> = {
  RESTORE_RUN_FINISHED: ["This game has finished.", "Explore its decisions or start a new run."],
  RESTORE_ALREADY_FINISHED: ["This game has already been continued to completion.", "Open the completed continuation."],
  RESTORE_SOURCE_INCOMPATIBLE: ["The recorded game or agent code is incompatible with this release.", "Use a release with verified support for this recorded code. Refreshing cannot make it compatible."],
  AGENT_PROTOCOL_IMPLEMENTATION_CHANGED: ["The recorded agent protocol differs from the current executor.", "Use the original compatible release or a separately verified compatible update."],
  AGENT_PROTOCOL_KNOWLEDGE_CHANGED: ["The recorded agent knowledge cannot be reproduced by this release.", "Use a release compatible with the recorded protocol and knowledge."],
  WORKER_BUSY: ["The worker is running another task.", "Wait for that task to finish, then check again."],
  RESTORE_CHECKPOINT_MISSING: ["There is no usable checkpoint for this run.", "Explore the recorded decisions. Recovery needs a retained, verified checkpoint."],
  RESTORE_UNSETTLED_ACTION: ["The last action has no verified settled state.", "Inspect the last decision. A verified recovery boundary is required before continuing."],
  RESTORE_CONTINUATION_UNFINISHED: ["An existing continuation has not finished.", "Open that continuation or wait for it to resolve."],
  BUDGET_EXTENSION_CHILD_UNRESOLVED: ["An existing budget continuation has not finished.", "Open that continuation before requesting another."],
  RECOVERY_FIXTURE_NOT_SUPPORTED: ["Evaluator fixtures cannot be restored as ordinary runs.", "Explore the fixture's recorded decisions."],
  RESTORE_CAMPAIGN_NOT_SUPPORTED: ["This campaign run cannot use standalone recovery.", "Review the campaign's supported continuation workflow."],
  RESTORE_INTERVENTION_NOT_SUPPORTED: ["This intervention does not support ordinary recovery.", "Explore the branch and its recorded provenance."],
  BUDGET_EXTENSION_REQUIRES_STANDALONE_ROOT: ["This run cannot use a standalone budget extension.", "Open the original standalone run, or review the campaign or branch workflow."],
  PARENT_NOT_COST_EXHAUSTED: ["This run did not stop at a supported dollar ceiling.", "Inspect its stop reason and use checkpoint recovery if available."],
  PROVIDER_CALL_LIMIT: ["The provider call allowance is exhausted.", "A dollar override does not increase the call allowance. Review the run or start a new separately authorized run."],
  GAME_ACTION_LIMIT: ["The game action allowance is exhausted.", "Review the run or start a new run with appropriate limits."],
  PAID_EXECUTION_NOT_AUTHORIZED: ["Paid continuation is not enabled for this configuration.", "Review the recorded provider configuration and explicit spending authorization."],
  EPISODE_COST_CAP: ["There is not enough episode budget for another call.", "Review a budget continuation if this is a standalone run stopped at its dollar ceiling."],
  CAMPAIGN_COST_CAP: ["There is not enough campaign budget for another call.", "Review the campaign budget before any continuation."],
  BUDGET_EXTENSION_CAP_ALREADY_SPENT: ["The proposed allowance has already been spent.", "Review the latest all-attempt spend and the available override choices."],
  BUDGET_EXTENSION_BELOW_RESERVATION: ["The additional allowance cannot cover the next call's reservation.", "Review the available override choices and their total ceiling."],
  EPISODE_CAP_BELOW_RESERVATION: ["The episode ceiling cannot cover the next call's reservation.", "Review the available override choices and their total ceiling."],
  BUDGET_INCREMENT_EXCEEDS_CAP_LIMIT: ["The additional allowance exceeds the supported ceiling.", "Review the available override choices."],
  BUDGET_EXTENSION_MUST_INCREASE_CAP: ["This allowance does not increase the recorded ceiling.", "Choose an available override that increases the ceiling."],
};

export function recoveryMessage(reason: string | null | undefined): [string, string] {
  if (reason && messages[reason]) return messages[reason];
  if (/RUNTIME|ENVIRONMENT|PROFILE|RELEASE/.test(reason || ""))
    return ["The required game runtime or environment is unavailable or incompatible.", "Use the retained compatible runtime and environment, then check again."];
  if (/SOURCE|COMPATIBILITY|PROTOCOL|CHECKPOINT_KIND/.test(reason || ""))
    return ["The retained recovery evidence is incompatible or cannot be verified.", "Use the recorded compatible release and retained evidence. Refreshing cannot repair incompatible code."];
  if (/LEDGER|CHECKPOINT|EVIDENCE|TERMINAL/.test(reason || ""))
    return ["The recorded recovery boundary or spending evidence could not be verified.", "Inspect the last decision and retained evidence before attempting recovery."];
  return ["A safe continuation is unavailable for this run.", "Inspect the technical reason and the last decision to determine a supported recovery path."];
}

export function RecoveryRefusal({ reason, title = "Restore unavailable" }: { reason?: string | null; title?: string }) {
  const [message, nextStep] = recoveryMessage(reason);
  return <div className="recovery-refusal"><p role="status">{title}: {message}</p><p className="muted">{nextStep}</p>{reason && <details><summary>Technical reason</summary><code>{reason}</code></details>}</div>;
}
