"""Layer-metadata CRUD + per-layer state get/set + mask-gap checking.

Every function takes the UIController instance as its first parameter (``ctrl``).
"""

from .undo_manager import skin_transaction


def create_layer(ctrl, name: str) -> int:
    meta, new_idx = ctrl._layer_mgr.create_layer(ctrl.storage.read_meta_list(), name)
    ctrl.storage.write_meta_list(meta)
    ctrl.storage.write_layer_dict(new_idx, {})
    switch_to_layer(ctrl, new_idx)
    return new_idx


def remove_layer(ctrl, index: int):
    """Remove a layer by slot index. No minimum is enforced any more -- the
    very last layer can be removed too, leaving an empty (but still
    present, ``"ss_layers_meta" in mesh`` stays True) Layer list. The
    flatten/composite pipeline already treats an empty layer list as "zero
    weight everywhere", the same result as every layer being hidden, so
    nothing downstream needs a real layer to fall back on."""
    meta = ctrl._layer_mgr.remove_layer(ctrl.storage.read_meta_list(), index)
    ctrl.storage.write_meta_list(meta)
    ctrl.storage.delete_layer_property(index)
    ctrl.storage.delete_mask_property(index)

    if not meta:
        # Nothing left to switch to -- re-flatten against the empty list and
        # leave the active-layer pointer as-is until the next Layer is created.
        ctrl._finish()
        ctrl.check_for_mask_gaps()
    elif ctrl.active_layer_index == index:
        switch_to_layer(ctrl, meta[0]["index"])
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
    switch_to_layer(ctrl, new_idx)
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

    others = sorted((i for i in selected_indices if i != target_index), reverse=True)

    ctrl.storage.write_layer_dict(target_index, merged_weight_dict)
    if merged_mask_dict:
        ctrl.storage.write_mask_dict(target_index, merged_mask_dict)
    else:
        # Degenerate case: every selected layer had no mask coverage.
        ctrl.storage.delete_mask_property(target_index)
    # Highest slot first to avoid index shifts within the loop.
    for idx in others:
        ctrl.storage.delete_layer_property(idx)
        ctrl.storage.delete_mask_property(idx)

    ctrl.storage.write_meta_list(new_meta_list)

    if ctrl.active_layer_index != target_index:
        switch_to_layer(ctrl, target_index)
    else:
        ctrl._finish()
        _reload_wp_temp_vgs(ctrl)
    ctrl.check_for_mask_gaps()
    return True


@skin_transaction(color_only=False, check_mask_gaps=True)
def toggle_visible(ctrl, index: int):
    meta = ctrl._layer_mgr.toggle_visible(ctrl.storage.read_meta_list(), index)
    ctrl.storage.write_meta_list(meta)


def rename_layer(ctrl, index: int, new_name: str):
    meta = ctrl._layer_mgr.rename_layer(ctrl.storage.read_meta_list(), index, new_name)
    ctrl.storage.write_meta_list(meta)


def get_layer_icon(ctrl, index: int = None) -> str:
    if index is None:
        index = ctrl.active_layer_index
    return ctrl._layer_mgr.get_icon(ctrl.storage.read_meta_list(), index)


def set_layer_icon(ctrl, index: int, icon: str):
    meta = ctrl._layer_mgr.set_icon(ctrl.storage.read_meta_list(), index, icon)
    ctrl.storage.write_meta_list(meta)


def switch_to_layer(ctrl, index: int):
    if index == ctrl.active_layer_index:
        return

    try:
        ctrl.ctx.scene.superskin_internal_transaction = True
    except Exception:
        pass

    try:
        from ..layer_storage.temp_vg_bridge import is_wp_session

        if is_wp_session(ctrl.obj):
            _switch_wp_layer(ctrl, index)
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


def _switch_wp_layer(ctrl, new_index: int):
    """Weight Paint session: recomposite, then swap the temp VGs (the paint
    surface) from the outgoing Layer to the incoming one."""
    from ..layer_storage.temp_vg_bridge import (
        delete_temp_vgs, layer_mask_default, load_layer_to_temp_vgs, pull_temp_to_storage,
    )

    obj = ctrl.obj
    old_index = ctrl.active_layer_index

    # A Layer removed or merged away already had its storage deleted; pulling
    # into it would resurrect the slot.
    if any(layer.get("index") == old_index for layer in ctrl.storage.read_meta_list()):
        pull_temp_to_storage(obj, ctrl.storage)

    ctrl._save_current_layer_state()
    ctrl.active_layer_index = new_index
    ctrl._flatten_to_mesh()
    ctrl._restore_layer_state()

    _, id_to_bone = ctrl.storage.get_unified_mapping(obj)
    delete_temp_vgs(obj, layer_idx=old_index)
    load_layer_to_temp_vgs(obj, ctrl.storage.read_layer_dict(new_index),
                           ctrl.storage.read_mask_dict(new_index), new_index, id_to_bone,
                           layer_mask_default(ctrl.storage, new_index))
    ctrl.apply_active_bone()


def _reload_wp_temp_vgs(ctrl):
    from ..layer_storage.temp_vg_bridge import is_wp_session, reload_active_layer_temp_vgs
    if is_wp_session(ctrl.obj):
        reload_active_layer_temp_vgs(ctrl.obj, ctrl.storage)
        ctrl.apply_active_bone()


def layer_meta_list(ctrl) -> list:
    return ctrl.storage.read_meta_list()


def get_bone_locks(ctrl, layer_index: int = None) -> dict:
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    return ctrl._layer_mgr.get_bone_locks(ctrl.storage.read_meta_list(), layer_index)


def set_bone_locks(ctrl, bone_locks: dict, layer_index: int = None):
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    meta = ctrl._layer_mgr.set_bone_locks(ctrl.storage.read_meta_list(), layer_index, bone_locks)
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
    meta = ctrl._layer_mgr.set_selected_bones(ctrl.storage.read_meta_list(), layer_index, selected_names)
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
    meta = ctrl._layer_mgr.set_active_bone_name(ctrl.storage.read_meta_list(), layer_index, name)
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
            from ..layer_storage.temp_vg_bridge import mask_vg_name
            storage.last_clicked_index = -1
            mask_vg = obj.vertex_groups.get(mask_vg_name(ctrl.active_layer_index))
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
    # Point the native active VG at the paint surface, so native brushes and
    # the built-in Vertex Group Weight Overlay act on the active Layer.
    try:
        if obj.mode == 'WEIGHT_PAINT':
            from ..layer_storage.temp_vg_bridge import weight_vg_name
            bone_to_id, _ = ctrl.storage.get_unified_mapping(obj)
            bone_id = bone_to_id.get(name)
            if bone_id is not None:
                temp_vg = obj.vertex_groups.get(weight_vg_name(ctrl.active_layer_index, bone_id))
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
    ctrl.storage.write_meta_list([{"name": "Base", "index": 0, "visible": True, "bone_locks": {}, "icon": "NONE",
                                     "mask_default": 1.0}])
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
        msg = "Mask Gap Detected: please recheck Layer Mask / Skin Weight coverage for gaps."
        print(f"\n[SuperSkinPro ERROR] {msg}\n")
        ctrl.show_report(msg)
        return True
    return False
