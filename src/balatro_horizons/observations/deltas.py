"""Neutral last-action feedback, derived exclusively from two public observations."""

from balatro_horizons.contracts import LastAction, PublicFieldChange, PublicObjectReference

AREAS = ("hand", "jokers", "consumables", "offers", "revealed_blinds")


def last_action(before, after, action):
    """Observed changes are not a score prediction, hand classification, or causal claim.

    Only an Action is accepted, not an envelope with model-authored notes/memory.
    References use the pre-action label when a selected handle has left the state.
    """
    selected_ids = set()
    for name, value in action.model_dump(mode="json").items():
        if name.endswith("_ids"):
            selected_ids.update(value)
        elif name.endswith("_id"):
            selected_ids.add(value)
    selected, added, removed, changes = [], [], [], []

    def change(path, old, new):
        if old != new:
            changes.append(PublicFieldChange(path=path, before=old, after=new))

    change(["phase"], before.phase, after.phase)
    old_state = before.state.model_dump(mode="json")
    new_state = after.state.model_dump(mode="json")
    for section in ("progress", "resources", "hand_levels"):
        old, new = old_state[section], new_state[section]
        for key in sorted(old.keys() | new.keys()):
            change([section, key], old.get(key), new.get(key))
    for section in ("persistent_effects", "owned_vouchers", "pending_tags"):
        change(
            [section],
            old_state[section],
            new_state[section],
        )
    for area in AREAS:
        old = {obj.id: obj for obj in getattr(before.state, area)}
        new = {obj.id: obj for obj in getattr(after.state, area)}

        def reference(obj, area=area):
            return PublicObjectReference(area=area, id=obj.id, label=obj.label)

        selected.extend(reference(obj) for key, obj in old.items() if key in selected_ids)
        removed.extend(reference(obj) for key, obj in old.items() if key not in new)
        added.extend(reference(obj) for key, obj in new.items() if key not in old)
        if old.keys() == new.keys():
            change([area, "order"], list(old), list(new))
        for key, obj in old.items():
            if key not in new:
                continue
            old_fields, new_fields = obj.model_dump(mode="json"), new[key].model_dump(mode="json")
            for field in sorted(old_fields.keys() | new_fields.keys()):
                change([area, key, field], old_fields.get(field), new_fields.get(field))
    return LastAction(
        from_observation_id=before.observation_id,
        to_observation_id=after.observation_id,
        action_type=action.type,
        selected_objects=selected,
        added_objects=added,
        removed_objects=removed,
        changes=changes,
    )
