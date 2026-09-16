---
name: balatro-validation
description: Diagnose a Balatro action or scoring discrepancy using public state, actual tool schemas and explicitly bounded evidence.
---

# Check the relevant discrepancy

Check the smallest set of public facts needed to explain the discrepancy.

For an action mismatch, check the current observation, phase, offered tool schema, IDs, target count/position, forced selection, resources, capacity and permissions. Distinguish a protocol rejection from a legal submission that scores zero. In Horizons, unresolved execution after a timeout ends the episode as an infrastructure failure; the playing model must not retry a possibly executed action. Operator reconciliation belongs outside the playing policy.

For a score mismatch, inspect classification, level/base values, scoring subset, debuffs, pre-scoring changes, card events/retriggers, held effects, independent Jokers/editions/copies, final deck rule and rounding. A random outcome alone does not establish a rule change. Correct arithmetic on an incorrect phase model is still a modeling error.

Label each relevant conclusion supported, contradicted or unresolved. Use [harness tools](../balatro-orchestrator/references/harness.md) to retrieve available public evidence and rules. Keep an estimate uncertain when the necessary scoring detail is unavailable.

If the available public evidence cannot resolve a mechanic, use a conservative estimate or a different supported line. Consult [known limits](../../KNOWN_LIMITS.md) for the specific gaps; keep unseen outcomes and private setup information out of the decision.
