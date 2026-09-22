"""Link an action-attached edit to its saved mutation and committed action."""

from balatro_horizons.contracts import ActionEnvelope
from balatro_horizons.harness.context.memory import VERSION, valid_key


class ActionNoteLink:
    """Fold existing journal events without changing execution or the notebook.

    A proposal alone is not evidence that a note was saved. A saved note alone
    is not evidence that its associated action executed. Only a matching commit
    releases the small, allowlisted receipt; the next observation supplies results.
    """

    def __init__(self):
        self.proposal = self.saved = None

    def consume(self, event):
        kind = event["type"]
        if kind == "agent_operation":
            self.proposal, self.saved = event, None
        elif kind == "run_note":
            self.saved = event
        elif kind == "action_commit":
            receipt = self._receipt(event)
            self.proposal = self.saved = None
            return receipt
        elif kind in ("observation", "helper_result", "action_rejected",
                      "action_status_unknown", "harness_failure", "terminal"):
            self.proposal = self.saved = None
        return None

    def _receipt(self, commit):
        if self.proposal is None or self.saved is None:
            return None
        scope = (commit["episode_id"], commit["observation_id"])
        if any((event.get("episode_id"), event.get("observation_id")) != scope
               for event in (self.proposal, self.saved)):
            return None
        operation = self.proposal["payload"].get("operation")
        if not isinstance(operation, dict) or operation.get("kind") != "action":
            return None
        edit = operation.get("note_update")
        if (not isinstance(edit, dict) or set(edit) != {"key", "text"}
                or not valid_key(edit["key"])
                or (edit["text"] is not None and not isinstance(edit["text"], str))):
            return None
        mutation = self.saved["payload"]
        expected_kind = "delete_run_note" if edit["text"] is None else "set_run_note"
        if (mutation.get("version") != VERSION or mutation.get("operation") != expected_kind
                or mutation.get("key") != edit["key"] or mutation.get("text") != edit["text"]
                or type(mutation.get("revision")) is not int):
            return None
        try:
            envelope = ActionEnvelope.model_validate(operation.get("envelope"))
        except ValueError:
            return None
        if envelope.observation_id != scope[1] or envelope.model_dump(mode="json") != commit["payload"]:
            return None
        return {
            "operation": expected_kind, "key": edit["key"], "text": edit["text"],
            "revision": mutation["revision"], "timing": "before_action_execution",
            "operation_event_id": self.proposal["event_id"],
            "note_event_id": self.saved["event_id"],
        }
