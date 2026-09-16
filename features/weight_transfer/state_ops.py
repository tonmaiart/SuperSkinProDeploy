"""Unified Source/Target list management for the Weight Transfer popup.

Operates on `context.scene.superskin_weight_transfer_state` (SSWeightTransferState,
defined in weight_transfer_feature.py) — the live/working Source+Target list
for object.mw_copy_skin_weight_maya. Mirrors the plain add/remove-by-index
UIList pattern used by features/mirror/ops.py's SUPERSKIN_OT_add_mirror_sr /
remove_mirror_sr.

Which row is "the Source" is a per-row `is_source` BoolProperty
(SSWeightTransferEntryItem), not a separate list — see
_on_is_source_changed() in weight_transfer_feature.py for the radio-button
enforcement (only one entry may have it set at a time). This module has no
"assign source" operator; the toggle itself, drawn inline in the UIList row,
is the whole interaction.

This module also owns the viewport<->list selection sync's REVERSE half
(sync_active_entry_from_viewport() below) and the shared guard flag both
directions check, since it has no import relationship with either
weight_transfer_feature.py or ui.py — putting the guard here lets both of
those import this module without creating a circular import (both already
need to reach into `state_ops` for other things anyway).
"""

import bpy

from ...core.facade import CoreFacade

_syncing_viewport_selection = False
_pending_sync_index = None


def sync_active_entry_from_viewport(context):
    """Reverse direction of weight_transfer_feature.py's
    _on_active_entry_changed() (which pushes a list-row click out to the
    viewport selection): called once per redraw of the Transfer tab
    (ui.py's _draw_transfer_tab()) so that selecting a mesh in the 3D
    viewport highlights the matching row in the unified Source/Target list.

    Does NOT write `active_entry_index` directly — Blender raises
    `AttributeError: Writing to ID classes in this context is not allowed`
    for any attempt to write Scene-owned data (which `state` is, being a
    PointerProperty on `bpy.types.Scene`) from inside a Panel's draw() call
    stack (confirmed at runtime). Instead this only *detects* a mismatch and
    schedules the actual write via `bpy.app.timers.register(..., first_interval=0.0)`
    — the standard, lightweight workaround: the write happens on the very
    next event-loop tick, outside any draw() call, where it's allowed, with
    no perceptible delay to the user. `_pending_sync_index` de-dupes so a
    burst of redraws before the timer fires doesn't queue up N redundant
    timers for the same target index.

    The eventual write (in _apply_pending_sync()) is guarded by
    _syncing_viewport_selection so it doesn't bounce back into re-mutating
    the viewport selection via _on_active_entry_changed, which would
    silently collapse a multi-object viewport selection down to just the
    one matched object.
    """
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
    """Runs the deferred write scheduled by sync_active_entry_from_viewport()
    above, on the next event-loop tick — see that function's docstring for
    why this can't just be a direct assignment at detection time. Returning
    `None` (not a float) tells `bpy.app.timers` to run this once and not
    reschedule itself."""
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
    """True only while _apply_pending_sync() above is assigning
    active_entry_index. weight_transfer_feature.py's _on_active_entry_changed
    checks this first and returns immediately if it's True, which is what
    breaks the otherwise-infinite feedback loop between the two sync
    directions."""
    return _syncing_viewport_selection


class SUPERSKIN_OT_wt_add_entry(bpy.types.Operator):
    """Add every currently selected mesh (in the viewport) to the unified
    Source/Target list, skipping anything already present. New rows always
    start with is_source=False — mark one as the Source afterward via the
    row's own toggle."""
    bl_idname = "superskin.wt_add_entry"
    bl_label = "Add Selected Meshes"
    bl_options = {'REGISTER'}

    def execute(self, context):
        state = context.scene.superskin_weight_transfer_state
        selected_meshes = [obj for obj in context.selected_objects if obj.type == 'MESH']

        if not selected_meshes:
            self.report({'WARNING'}, "เลือก Mesh ใน viewport ก่อนกด Add")
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
            self.report({'WARNING'}, "Mesh ที่เลือกอยู่ในลิสต์อยู่แล้วทั้งหมด")
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
