
import bpy

from ...core.facade import CoreFacade
from ...interface.template_ui import SuperSkinListMixin, resolve_row_click_selection
from ...interface.utils.utils import _has_layer_system

_syncing_viewport_selection = False
_pending_sync_index = None
_last_clicked_name = None
_click_history = []


def sync_active_entry_from_viewport(context):
    global _pending_sync_index
    state = getattr(context.scene, "superskin_weight_transfer_state", None)
    if state is None:
        return
    if _stale_entry_indices(context.scene):
        _schedule_prune()
        return
    active_obj = context.active_object
    if active_obj is None:
        return
    match_index = next((i for i, e in enumerate(state.entries) if e.object == active_obj), None)
    if match_index is None or match_index == state.active_entry_index:
        return
    if _pending_sync_index == match_index:
        return

    _pending_sync_index = match_index
    bpy.app.timers.register(_apply_pending_sync, first_interval=0.0)


def _apply_pending_sync():
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


_prune_scheduled = False


def _stale_entry_indices(scene):
    state = scene.superskin_weight_transfer_state
    return [
        i for i, e in enumerate(state.entries)
        if e.object is None or e.object.name not in scene.objects
    ]


def _schedule_prune():
    global _prune_scheduled
    if not _prune_scheduled:
        _prune_scheduled = True
        bpy.app.timers.register(_apply_prune, first_interval=0.0)


def _apply_prune():
    global _prune_scheduled
    _prune_scheduled = False
    scene = bpy.context.scene
    state = getattr(scene, "superskin_weight_transfer_state", None)
    if state is None:
        return None
    stale = _stale_entry_indices(scene)
    if not stale:
        return None
    active = state.active_entry_index
    for i in reversed(stale):
        state.entries.remove(i)
    active -= sum(1 for i in stale if i < active)
    _set_active_index_quietly(state, max(0, min(active, len(state.entries) - 1)))
    CoreFacade.debug_log("feature_domains", f"weight_transfer: pruned {len(stale)} stale entries")
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
    return None


def is_syncing_viewport_selection():
    return _syncing_viewport_selection


class SUPERSKIN_OT_wt_add_entry(bpy.types.Operator):
    """Add the selected meshes to the list"""
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


def can_be_source(obj) -> bool:
    if obj is None:
        return False
    return _has_layer_system(obj) or any(m.type == 'ARMATURE' and m.object for m in obj.modifiers)


def is_entry_selected(entry) -> bool:
    obj = entry.object
    return obj is not None and obj.name in bpy.context.view_layer.objects and obj.select_get()


def entry_key(entry) -> str:
    return entry.object.name if entry.object else f"missing:{entry.as_pointer()}"


def entry_search_text(entry) -> str:
    return entry.object.name if entry.object else "(missing mesh)"


_sort_alpha = False


def entry_display_order(entries, sort_alpha):
    global _sort_alpha
    _sort_alpha = sort_alpha
    order = list(range(len(entries)))
    if sort_alpha:
        order.sort(key=lambda i: entry_search_text(entries[i]).lower())
    return order


class _EntryVisibilityHooks(SuperSkinListMixin):
    """Reproduces the list's filtered row order outside its draw call."""

    def get_item_key(self, item):
        return entry_key(item)

    def get_search_text(self, item):
        return entry_search_text(item)

    def get_display_order(self, context, data):
        return entry_display_order(data.entries, _sort_alpha)


_entry_visibility_hooks = _EntryVisibilityHooks()


def select_all_visible_entries(context) -> bool:
    global _last_clicked_name, _click_history
    state = context.scene.superskin_weight_transfer_state
    view_objects = context.view_layer.objects
    visible = _entry_visibility_hooks.compute_visible_keys(context, state, "entries")
    listed = [view_objects[k] for k in visible if k in view_objects]
    if not listed:
        return False
    for obj in listed:
        obj.select_set(True)
    _click_history = [obj.name for obj in listed]
    if _last_clicked_name not in _click_history:
        _last_clicked_name = _click_history[-1]
    return True


def _set_active_index_quietly(state, index):
    global _syncing_viewport_selection
    _syncing_viewport_selection = True
    try:
        state.active_entry_index = index
    finally:
        _syncing_viewport_selection = False


class SUPERSKIN_OT_wt_select_entry(bpy.types.Operator):
    """Select this mesh (Ctrl: toggle, Shift: range, Alt+Shift: all)"""
    bl_idname = "superskin.wt_select_entry"
    bl_label = "Select Mesh"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty()

    def invoke(self, context, event):
        global _last_clicked_name, _click_history
        state = context.scene.superskin_weight_transfer_state
        if not (0 <= self.index < len(state.entries)):
            return {'CANCELLED'}
        clicked = state.entries[self.index].object
        view_objects = context.view_layer.objects
        if clicked is None or clicked.name not in view_objects:
            return {'CANCELLED'}

        visible_keys = _entry_visibility_hooks.compute_visible_keys(context, state, "entries")
        if clicked.name not in visible_keys:
            visible_keys.append(clicked.name)
        listed = [view_objects[k] for k in visible_keys if k in view_objects]
        visual_order = [obj.name for obj in listed]
        selected = {obj.name for obj in listed if obj.select_get()}
        history = [name for name in _click_history if name in selected]
        last = _last_clicked_name if _last_clicked_name in visual_order else None
        selected, last, history = resolve_row_click_selection(
            selected, last, history, clicked.name, visual_order, event,
        )
        _last_clicked_name, _click_history = last, history

        modifier = event.ctrl or event.shift
        if not modifier:
            for other in context.selected_objects:
                other.select_set(False)
        for obj in listed:
            obj.select_set(obj.name in selected)
        if clicked.name in selected:
            view_objects.active = clicked
        _set_active_index_quietly(state, self.index)

        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


class _ToggleEntryFlag:
    """Flips one row's flag; on a selected row every selected row gets the clicked row's new value."""
    bl_options = {'INTERNAL', 'UNDO'}
    flag = ""

    index: bpy.props.IntProperty()

    @classmethod
    def description(cls, context, properties):
        state_rna = bpy.types.Scene.bl_rna.properties["superskin_weight_transfer_state"].fixed_type
        entry_rna = state_rna.properties["entries"].fixed_type
        return entry_rna.properties[cls.flag].description

    def execute(self, context):
        state = context.scene.superskin_weight_transfer_state
        if not (0 <= self.index < len(state.entries)):
            return {'CANCELLED'}
        clicked = state.entries[self.index]
        value = not getattr(clicked, self.flag)
        targets = [clicked]
        if is_entry_selected(clicked):
            targets = [e for e in state.entries if is_entry_selected(e)]
        if self.flag == 'is_source' and value:
            targets = [e for e in targets if can_be_source(e.object)]
        for entry in targets:
            setattr(entry, self.flag, value)
        return {'FINISHED'}


class SUPERSKIN_OT_wt_toggle_source(_ToggleEntryFlag, bpy.types.Operator):
    bl_idname = "superskin.wt_toggle_source"
    bl_label = "Is Source"
    flag = 'is_source'


class SUPERSKIN_OT_wt_toggle_selected_verts(_ToggleEntryFlag, bpy.types.Operator):
    bl_idname = "superskin.wt_toggle_selected_verts"
    bl_label = "Use Selected Vertices Only"
    flag = 'use_selected_verts'


class SUPERSKIN_OT_wt_remove_entry(bpy.types.Operator):
    """Remove the selected meshes from the list"""
    bl_idname = "superskin.wt_remove_entry"
    bl_label = "Remove Mesh"
    bl_options = {'REGISTER'}

    def execute(self, context):
        state = context.scene.superskin_weight_transfer_state
        indices = [i for i, e in enumerate(state.entries) if is_entry_selected(e)]
        if not indices and 0 <= state.active_entry_index < len(state.entries):
            indices = [state.active_entry_index]
        if not indices:
            self.report({'WARNING'}, "No entry selected")
            return {'CANCELLED'}

        removed_names = []
        for i in reversed(indices):
            obj = state.entries[i].object
            removed_names.append(obj.name if obj else "(empty)")
            state.entries.remove(i)
        new_index = max(0, min(indices[0], len(state.entries) - 1))
        _set_active_index_quietly(state, new_index)

        CoreFacade.debug_log("feature_domains", f"weight_transfer.wt_remove_entry(): removed {removed_names!r}")
        return {'FINISHED'}


class SUPERSKIN_OT_wt_clear_entries(bpy.types.Operator):
    """Remove every mesh from the list"""
    bl_idname = "superskin.wt_clear_entries"
    bl_label = "Clear List"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        state = getattr(context.scene, "superskin_weight_transfer_state", None)
        return state is not None and len(state.entries) > 0

    def execute(self, context):
        state = context.scene.superskin_weight_transfer_state
        count = len(state.entries)
        state.entries.clear()
        state.active_entry_index = 0

        CoreFacade.debug_log("feature_domains", f"weight_transfer.wt_clear_entries(): removed {count} entries")
        return {'FINISHED'}


_classes = (
    SUPERSKIN_OT_wt_add_entry,
    SUPERSKIN_OT_wt_select_entry,
    SUPERSKIN_OT_wt_toggle_source,
    SUPERSKIN_OT_wt_toggle_selected_verts,
    SUPERSKIN_OT_wt_remove_entry,
    SUPERSKIN_OT_wt_clear_entries,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
