# Decision explorer Dev mode

Open a run's **Explore decisions** view and enable **Dev mode**. It is off by
default. Each selected decision gets a chronological list of provider requests,
including helper calls, invalid responses, transport retries, and requests still
waiting for a response. Decisions that have started model work but have not
requested a gameplay action appear only while Dev mode is enabled; they are not
counted as committed actions or added to existing decision exports.

Each provider request shows returned tool names and call IDs, complete arguments,
matched tool results delivered in later requests, journaled helper results,
validation feedback, note edits, timestamps and recorded cost/usage where
available. Expand individual sections to inspect the recorded request, response,
delivered context, public before/after state, or all decision events. The delivered
context includes the actual costs, notebook and working memory sent for that call.
Malformed argument strings remain inspectable and are not repaired.

The readable view presents each request with public card names and modifiers,
its recorded upfront price or sale quote, and the model's note separately from
the observed result. It shows helper returns, saved notebook edits, rejection
feedback, and API token/cost accounting without requiring JSON inspection.
Prices come only from the context delivered for that request; unknown quotes
stay unknown. Resource changes compare the decision's public before/after states
and are not attributed to individual helper calls. This is deterministic
presentation, not a second model evaluating the decisions.

Open **What the model was given** for readable costs, notebook and recent working
memory. Long text and nested results have expansion controls; unknown tool fields
remain accessible. **Technical details** retains full recorded JSON and IDs.
Text is never executed as HTML, and no new model calls are made to summarize it.

Provider-native call IDs link tool results returned in a subsequent request.
Harness journal events without those IDs are explicitly associated by request
order within the decision, not presented as proof that every returned operation
executed. Multiple returned calls may all be rejected. A commit without a settled
observation is not shown as a completed transition. Provider-returned summaries
are inspectable; hidden internal reasoning and opaque continuation bytes are not.

The inspector uses a separate read-only endpoint:
`GET /api/review/decisions/{decision}/trace`. A retrospective review token is
required on the server, including when a prospective session has prior exposure.
Viewing the trace records review exposure without moving the annotation cursor.
Only the selected decision segment and its immediate public result are returned;
later decisions' calls are excluded. Opaque continuations are removed and the
result passes the existing privacy scanner, including the private seed check.
No credentials, private engine state or checkpoints are inputs to the display.

Live requests are serialized and refresh only while Dev mode and live updates are
enabled, the tab is visible, and the selected decision remains open. Completed
decisions stop polling; **Refresh decisions** explicitly reloads them. Switching
decisions or disabling the mode aborts pending inspector fetches. JSON renders as
escaped, wrapping text only when expanded. It cannot execute model-authored HTML.

This feature changes read-only review and frontend code. It does not change model
inputs, gameplay, native execution, budgets, or annotations. Offline backend and
browser tests are the verification gate; native certification is unnecessary.

Offline tests cover both provider formats, ID-linked helper results, retries,
malformed arguments, pending calls, temporal and privacy boundaries, live
updates, selection races, phone layout, and escaped hostile text. The inspector
itself needs no native launch or paid-provider call. Action-outcome changes in
the same slice do change the harness identity and require their own evidence
review before deployment; the inspector does not certify them.
