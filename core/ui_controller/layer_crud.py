"""Layer-metadata CRUD + per-layer state get/set + mask-gap checking.

Every function takes the UIController instance as its first parameter (``ctrl``).
"""

import bpy
from .undo_manager import skin_transaction
from ...core_subsystems.topology_cache_manager import TopologyCacheManager


def create_layer(ctrl, name: str) -> int:
    meta, new_idx = ctrl._layer_mgr.create_layer(ctrl.storage.read_meta_list(), name)
    ctrl.storage.write_meta_list(meta)
    ctrl.storage.write_layer_dict(new_idx, {})
    switch_to_layer(ctrl, new_idx, push_undo=False)
    return new_idx


def remove_layer(ctrl, index: int):
    meta_list = ctrl.storage.read_meta_list()
    if len(meta_list) <= 1:
        return
    meta = ctrl._layer_mgr.remove_layer(meta_list, index)
    ctrl.storage.write_meta_list(meta)
    ctrl.storage.delete_layer_property(index)
    ctrl.storage.delete_mask_property(index)

    if ctrl.active_layer_index == index:
        next_fallback = meta[0]["index"] if meta else 0
        switch_to_layer(ctrl, next_fallback, push_undo=False)
    else:
        ctrl._finish()
        ctrl.check_for_mask_gaps()


@skin_transaction(color_only=False, check_mask_gaps=True)
def move_layer(ctrl, index: int, direction: int) -> bool:
    original = ctrl.storage.read_meta_list()
    meta = ctrl._layer_mgr.move_layer(original, index, direction)
    if meta == original:
        return False
    ctrl.storage.write_meta_list(meta)
    return True


def duplicate_layer(ctrl, index: int) -> int:
    meta, new_idx = ctrl._layer_mgr.duplicate_layer(ctrl.storage.read_meta_list(), index)
    if new_idx is None:
        return -1
    ctrl.storage.write_meta_list(meta)
    ctrl.storage.clone_layer_properties(index, new_idx)
    switch_to_layer(ctrl, new_idx, push_undo=False)
    return new_idx


def merge_selected_layers(ctrl, selected_indices: list, target_index: int) -> bool:
    """Bridge: harvest bpy data, call core_subsystems.layer_merge, then write back.

    Returns False when preconditions fail; True on success.
    """
    from ...core_subsystems.layer_compositor import LayerCompositor as _LC

    meta_list = ctrl.storage.read_meta_list()
    num_verts = len(ctrl.mesh.vertices)
    layer_data_map = ctrl.storage.harvest_layer_data_map()
    mask_data_map = ctrl.storage.harvest_mask_data_map()

    result = _LC.merge_selected(
        meta_list, layer_data_map, mask_data_map,
        selected_indices, target_index, num_verts,
    )
    if result is None:
        return False

    merged_weight_dict, merged_mask_dict, new_meta_list = result

    ctrl.storage.write_layer_dict(target_index, merged_weight_dict)
    if merged_mask_dict:
        ctrl.storage.write_mask_dict(target_index, merged_mask_dict)
    else:
        # Degenerate case: every selected layer had no mask coverage.
        ctrl.storage.delete_mask_property(target_index)

    # Delete raw storage for the removed layers (highest slot first to avoid
    # index shifts on subsequent deletions within the same loop).
    others = sorted((i for i in selected_indices if i != target_index), reverse=True)
    for idx in others:
        ctrl.storage.delete_layer_property(idx)
        ctrl.storage.delete_mask_property(idx)
    ctrl.storage.write_meta_list(new_meta_list)

    if ctrl.active_layer_index != target_index:
        switch_to_layer(ctrl, target_index, push_undo=False)
    else:
        ctrl._finish()
    ctrl.check_for_mask_gaps()
    return True


@skin_transaction(color_only=False, check_mask_gaps=True)
def toggle_visible(ctrl, index: int):
    meta = ctrl._layer_mgr.toggle_visible(ctrl.storage.read_meta_list(), index)
    ctrl.storage.write_meta_list(meta)


def rename_layer(ctrl, index: int, new_name: str):
    meta = ctrl._layer_mgr.rename_layer(ctrl.storage.read_meta_list(), index, new_name)
    ctrl.storage.write_meta_list(meta)


def switch_to_layer(ctrl, index: int, *, push_undo: bool = True):
    if index == ctrl.active_layer_index:
        return

    # push_undo parameter kept for API compatibility but is now a no-op
    # (Blender tracks the switch natively via temp VGs)

    try:
        ctrl.ctx.scene.superskin_internal_transaction = True
    except Exception:
        pass

    try:
        in_edit = ctrl.obj.mode == 'EDIT'

        if in_edit:
            _bake_and_reload_temp_vgs(ctrl, index)
        else:
            ctrl._save_current_layer_state()
            ctrl.active_layer_index = index
            ctrl._flatten_to_mesh()
            ctrl._restore_layer_state()

        ctrl.mesh.update()
        ctrl.obj.update_tag()
        ctrl.shader_mgr.bump_deform_generation()
        ctrl.refresh_visualizer_color_only()

    finally:
        try:
            ctrl.ctx.scene.superskin_internal_transaction = False
        except Exception:
            pass


def _bake_and_reload_temp_vgs(ctrl, new_layer_index: int):
    """In Edit Mode: save current temp VGs → ss_layer_N, load new layer → temp VGs.

    1. Exit Edit Mode (needed to write VG data from Python API)
    2. Read temp VGs → encode → write to ss_layer_N (save current layer)
    3. Delete temp VGs
    4. Load new layer from ss_layer_N → new temp VGs
    5. Re-enter Edit Mode

    Blender records one memfile undo step for this whole sequence (the mode
    bounce), and __ssp_meta records the new layer index so undo_post can
    restore the correct layer when Ctrl+Z fires.
    """
    import bpy
    from ..layer_storage.temp_vg_bridge import (
        has_temp_vgs, read_temp_vgs_to_layer,
        load_layer_to_temp_vgs, delete_temp_vgs
    )

    obj = ctrl.obj
    was_suppressing = ctrl.ctx.scene.superskin_internal_transaction
    ctrl.ctx.scene.superskin_internal_transaction = True

    try:
        bpy.ops.object.mode_set(mode='OBJECT')

        if has_temp_vgs(obj):
            layer_dict, mask_dict, _ = read_temp_vgs_to_layer(obj)
            old_layer_dict = ctrl.storage.read_layer_dict(ctrl.active_layer_index)
            ctrl.storage.write_layer_dict(ctrl.active_layer_index, layer_dict)
            if mask_dict:
                ctrl.storage.write_mask_dict(ctrl.active_layer_index, mask_dict)
            else:
                ctrl.storage.delete_mask_property(ctrl.active_layer_index)
            ctrl.purge_zeroed_orphans_after_bake(old_layer_dict, layer_dict)

        delete_temp_vgs(obj)

        ctrl._save_current_layer_state()
        ctrl.active_layer_index = new_layer_index

        new_layer_dict = ctrl.storage.read_layer_dict(new_layer_index)
        new_mask_dict = ctrl.storage.read_mask_dict(new_layer_index)
        _, id_to_bone = TopologyCacheManager.get_local_mapping(ctrl.obj, ctrl.storage)
        load_layer_to_temp_vgs(
            obj,
            new_layer_dict,
            new_mask_dict,
            new_layer_index,
            id_to_bone,
        )

        ctrl._restore_layer_state()

        bpy.ops.object.mode_set(mode='EDIT')

    finally:
        ctrl.ctx.scene.superskin_internal_transaction = was_suppressing
        # Guarantee re-entry into Edit Mode even if an intermediate step raised.
        if ctrl.obj.mode != 'EDIT':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except Exception:
                pass


def layer_meta_list(ctrl) -> list:
    return ctrl.storage.read_meta_list()


def get_bone_locks(ctrl, layer_index: int = None) -> dict:
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    return ctrl._layer_mgr.get_bone_locks(ctrl.storage.read_meta_list(), layer_index)


def set_bone_locks(ctrl, bone_locks: dict, layer_index: int = None):
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    meta = ctrl.storage.read_meta_list()
    meta = ctrl._layer_mgr.set_bone_locks(meta, layer_index, bone_locks)
    ctrl.storage.write_meta_list(meta)


def apply_bone_locks(ctrl):
    locks = get_bone_locks(ctrl)
    for item in ctrl.obj.superskin_bones_collection:
        item.lock_weight = locks.get(item.name, False)


def get_selected_bones(ctrl, layer_index: int = None) -> str:
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    return ctrl._layer_mgr.get_selected_bones(ctrl.storage.read_meta_list(), layer_index)


def set_selected_bones(ctrl, selected_names: str, layer_index: int = None):
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    meta = ctrl.storage.read_meta_list()
    meta = ctrl._layer_mgr.set_selected_bones(meta, layer_index, selected_names)
    ctrl.storage.write_meta_list(meta)


def apply_selected_bones(ctrl):
    sel = get_selected_bones(ctrl)
    if not sel or not sel.startswith(","):
        sel = "," + (sel or "")
    try:
        ctrl.obj.superskin_storage.selected_names = sel
    except Exception:
        pass


def get_active_bone_name(ctrl, layer_index: int = None) -> str:
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    return ctrl._layer_mgr.get_active_bone_name(ctrl.storage.read_meta_list(), layer_index)


def set_active_bone_name(ctrl, name: str, layer_index: int = None):
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    meta = ctrl.storage.read_meta_list()
    meta = ctrl._layer_mgr.set_active_bone_name(meta, layer_index, name)
    ctrl.storage.write_meta_list(meta)


def apply_active_bone(ctrl):
    """Route the native active-VG pointer to whatever the Deform Bones
    list's active row is: the Mask virtual row, or a real bone.

    ``obj.superskin_storage.active_is_mask`` is checked first and
    short-circuits — it is the single source of truth for "is the Mask row
    selected," extending the last_clicked_index / active_orphan_name
    tri-state from docs/bug-history/0003. ``scene.superskin_is_mask_mode``
    is written here as a derived side effect on every call, so the many
    other consumers of ``is_mask_context()`` keep working unchanged.
    """
    obj = ctrl.obj
    storage = obj.superskin_storage

    try:
        ctrl.ctx.scene.superskin_is_mask_mode = bool(storage.active_is_mask)
    except Exception:
        pass

    if storage.active_is_mask:
        try:
            storage.last_clicked_index = -1
            mask_vg = obj.vertex_groups.get("__ssp_m")
            if mask_vg is not None:
                obj.vertex_groups.active_index = mask_vg.index
        except Exception:
            pass
        return

    name = get_active_bone_name(ctrl)
    if not name:
        return
    vg = obj.vertex_groups.get(name)
    if vg is None:
        return
    try:
        storage.last_clicked_index = vg.index
    except Exception:
        pass
    # Also set Blender's native active_index so the built-in Vertex Group
    # Weight Overlay renders the correct weights without a custom GPU shader.
    try:
        if obj.mode == 'EDIT':
            bone_to_id, _ = ctrl.storage.get_unified_mapping(obj)
            bone_id = bone_to_id.get(name)
            if bone_id is not None:
                temp_vg = obj.vertex_groups.get(f"__ssp_{bone_id}")
                if temp_vg is not None:
                    obj.vertex_groups.active_index = temp_vg.index
        else:
            obj.vertex_groups.active_index = vg.index
    except Exception:
        pass


def enter_mask_editing_context(ctrl, active_vg_idx: int = -1):
    if active_vg_idx < 0:
        active_vg_idx = ctrl.obj.vertex_groups.active_index
    ctrl._flatten_to_mesh()
    ctrl.mesh.update()
    ctrl.obj.update_tag()
    for window in ctrl.ctx.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    area.spaces.active.overlay.show_vertex_group_weights = True
                except Exception:
                    pass


def exit_mask_editing_context(ctrl, active_vg_idx: int = -1):
    if active_vg_idx < 0:
        active_vg_idx = ctrl.obj.vertex_groups.active_index
    ctrl._flatten_to_mesh()
    ctrl.mesh.update()
    ctrl.obj.update_tag()
    for window in ctrl.ctx.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def get_active_layer_weights_for_display(ctrl) -> dict:
    """Return active layer weights directly mapped as String Keys."""
    layer_dict = ctrl.storage.read_active_layer_dict()
    idx_to_name = ctrl._idx_to_name()

    if not layer_dict:
        return {
            v.index: {idx_to_name[g.group]: g.weight
                      for g in v.groups
                      if g.group in idx_to_name and g.weight > 0.0}
            for v in ctrl.mesh.vertices
        }

    cleaned = {}
    for v_idx, weights in layer_dict.items():
        v_idx_int = int(v_idx)
        v_weights = {}
        for k_key, w in weights.items():
            if isinstance(k_key, int) or (isinstance(k_key, str) and k_key.isdigit()):
                g_idx = int(k_key)
                if g_idx in idx_to_name:
                    v_weights[idx_to_name[g_idx]] = float(w)
            else:
                v_weights[str(k_key)] = float(w)
        cleaned[v_idx_int] = v_weights
    return cleaned


def init_layer_system(ctrl) -> bool:
    if ctrl.storage.has_layer_system():
        return False
    ctrl.storage.write_meta_list([{"name": "Base", "index": 0, "visible": True, "bone_locks": {}}])
    ctrl.storage.set_active_layer_index(0)
    ctrl.storage.init_layer_0_from_live_weights(ctrl.obj)
    return True


def remove_layer_system(ctrl) -> bool:
    """Tear down the layer system on the active mesh, reverting it to a
    plain mesh with only native Vertex Group weights (no SuperSkinPro layer
    history). The real deform weights already baked onto the mesh are left
    untouched. Returns False if the mesh has no layer system to remove."""
    if not ctrl.storage.has_layer_system():
        return False
    ctrl.storage.remove_layer_system()
    return True


def check_for_mask_gaps(ctrl) -> bool:
    meta = ctrl.storage.read_meta_list()
    num_verts = len(ctrl.mesh.vertices)
    mask_dicts_map = {layer["index"]: ctrl.storage.read_mask_dict(layer["index"]) for layer in meta}
    gap_vertices = ctrl._layer_mgr.find_mask_gaps(meta, mask_dicts_map, num_verts)

    if gap_vertices:
        msg = f"⚠️ Mask Gap Detected! {len(gap_vertices)} vertices are unweighted."
        print(f"\n❌ [SuperSkinPro ERROR] {msg}\n")

        def draw_popup(self, context):
            self.layout.label(text=msg, icon='ERROR')

        bpy.context.window_manager.popup_menu(draw_popup, title="System Compositor Warning", icon='CANCEL')
        return True
    return False
