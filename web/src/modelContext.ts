/** Reconstruct delivered context without rewriting the immutable wire record. */
type Data = Record<string, any>;

function record(value: unknown): Data {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Data : {};
}

export function recordedModelContext(body: unknown): Data | null {
  const request = record(body);
  const messages = request.input ?? request.messages;
  if (!Array.isArray(messages)) return null;
  const content = messages.find((item) => record(item).role === "user")?.content;
  if (typeof content !== "string") return null;
  try {
    const context = record(JSON.parse(content));
    if (!Object.keys(record(context.observation)).length) return null;
    for (const message of messages) {
      const item = record(message);
      if (item.type === "function_call_output") applyContextUpdate(context, item.output);
      if (item.role !== "user") continue;
      if (typeof item.content === "string") applyContextUpdate(context, item.content);
      for (const block of Array.isArray(item.content) ? item.content : []) {
        const value = record(block);
        if (value.type === "tool_result") applyContextUpdate(context, value.content);
        if (value.type === "text") applyContextUpdate(context, value.text);
      }
    }
    return context;
  } catch {
    return null;
  }
}

function applyContextUpdate(context: Data, content: unknown) {
  if (typeof content !== "string") return;
  let envelope: Data;
  try { envelope = record(JSON.parse(content)); } catch { return; }
  const update = record(envelope.context_update ?? record(envelope.tool_error).context_update);
  for (const key of ["run_notebook", "permitted_tools"]) {
    if (key in update) context[key] = update[key];
  }
  for (const key of ["helper_status", "notebook_maintenance"]) {
    if (key in update) context[key] = { ...record(context[key]), ...record(update[key]) };
  }
  if ("remaining_budget" in update) context.observation.remaining_budget = {
    ...record(context.observation.remaining_budget), ...record(update.remaining_budget),
  };
  // Historical full-envelope records remain readable; new requests omit this metadata.
  if ("retrieval_context" in update) context.observation.retrieval_context = update.retrieval_context;
}
