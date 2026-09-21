"""Unified Source/Target list management for the Weight Transfer popup."""

import bpy

from ...core.facade import CoreFacade

_syncing_viewport_selection = False
_pending_sync_index = None


def sync_active_entry_from_viewport(context):
    """Reverse direction of weight_transfer_feature.py's _on_active_entry_changed() (which
    pushes a list-row click."""
    global _pending_sync_index
    state = getattr(context.scene, "superskin_weight_transfer_state", None)
    if state is None:
        return
    active_obj = context.active_object
    if active_obj is None:
        return
    match_index = next((i for i, e in enumerate(state.entries) if e.object == active_obj), None)
    if match_index is None or match_index == state.active_entry_index:
        return
    if _pending_sync_index == match_index:
        return  # already scheduled, no need to queue another timer

    _pending_sync_index = match_index
    bpy.app.timers.register(_apply_pending_sync, first_interval=0.0)


def _apply_pending_sync():
    """Runs the deferred write scheduled by sync_active_entry_from_viewport() above, on the
    next event-loop tick."""
    global _syncing_viewport_selection, _pending_sync_index
    match_index = _pending_sync_index
    _pending_sync_index = None
    if match_index is None:
        return None

    state = getattr(bpy.context.scene, "superskin_weight_transfer_state", None)
    if state is None or not (0 <= match_index < len(state.entries)):
        return None

    _syncing_viewport_selection = True
    try:
        state.active_entry_index = match_index
    finally:
        _syncing_viewport_selection = False
    return None


def is_syncing_viewport_selection():
    """True only while _apply_pending_sync() above is assigning active_entry_index."""
    return _syncing_viewport_selection


class SUPERSKIN_OT_wt_add_entry(bpy.types.Operator):
    """Add every currently selected mesh (in the viewport) to the unified Source/Target list,
    skipping anything already present."""
    bl_idname = "superskin.wt_add_entry"
    bl_label = "Add Selected Meshes"
    bl_options = {'REGISTER'}

    def execute(self, context):
        state = context.scene.superskin_weight_transfer_state
        selected_meshes = [obj for obj in context.selected_objects if obj.type == 'MESH']

        if not selected_meshes:
            self.report({'WARNING'}, "Select a mesh in the viewport before clicking Add")
            return {'CANCELLED'}

        existing = {e.object for e in state.entries if e.object}
        added_names = []
        for obj in selected_meshes:
            if obj in existing:
                continue
            new_entry = state.entries.add()
            new_entry.object = obj
            existing.add(obj)
            added_names.append(obj.name)

        if not added_names:
            self.report({'WARNING'}, "The selected meshes are already all in the list")
            return {'CANCELLED'}

        state.active_entry_index = len(state.entries) - 1

        CoreFacade.debug_log("feature_domains", f"weight_transfer.wt_add_entry(): added {added_names}")
        self.report({'INFO'}, f"Added {len(added_names)} mesh(es) to the list")
        return {'FINISHED'}


class SUPERSKIN_OT_wt_remove_entry(bpy.types.Operator):
    """Remove the active row from the unified Source/Target list."""
    bl_idname = "superskin.wt_remove_entry"
    bl_label = "Remove Mesh"
    bl_options = {'REGISTER'}

    def execute(self, context):
        state = context.scene.superskin_weight_transfer_state
        if not (0 <= state.active_entry_index < len(state.entries)):
            self.report({'WARNING'}, "No entry selected")
            return {'CANCELLED'}

        removed_name = state.entries[state.active_entry_index].object
        removed_name = removed_name.name if removed_name else "(empty)"
        state.entries.remove(state.active_entry_index)
        state.active_entry_index = max(0, min(state.active_entry_index, len(state.entries) - 1))

        CoreFacade.debug_log("feature_domains", f"weight_transfer.wt_remove_entry(): removed {removed_name!r}")
        return {'FINISHED'}


_classes = (
    SUPERSKIN_OT_wt_add_entry,
    SUPERSKIN_OT_wt_remove_entry,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
