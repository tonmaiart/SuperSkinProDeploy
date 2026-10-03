
from .undo_manager import skin_transaction
from ..viewport import tag_redraw_areas
from ..layer_storage.temp_vg_bridge import PREFIX as TEMP_VG_PREFIX


def create_layer(ctrl, name: str) -> int:
    meta, new_idx = ctrl._layer_mgr.create_layer(ctrl.storage.read_meta_list(), name)
    ctrl.storage.write_meta_list(meta)
    ctrl.storage.write_layer_dict(new_idx, {})
    switch_to_layer(ctrl, new_idx)
    return new_idx


def create_group(ctrl, name: str) -> int:
    meta, new_idx = ctrl._layer_mgr.create_group(ctrl.storage.read_meta_list(), name)
    ctrl.storage.write_meta_list(meta)
    switch_to_layer(ctrl, new_idx)
    return new_idx


def move_layers_to_group(ctrl, selected_indices: list, target_group_id) -> bool:
    original = ctrl.storage.read_meta_list()
    meta = ctrl._layer_mgr.move_layers_to_group(original, selected_indices, target_group_id)
    if meta == original:
        return False
    ctrl.storage.write_meta_list(meta)
    ctrl.finish()
    ctrl.check_for_mask_gaps()
    return True


def remove_layer(ctrl, index: int):
    original = ctrl.storage.read_meta_list()
    removed_is_group = any(l.get("index") == index and l.get("is_group") for l in original)
    removed_indices = (
        [index] + [l["index"] for l in original if l.get("group_id") == index]
        if removed_is_group else [index]
    )

    meta = ctrl._layer_mgr.remove_layer(original, index)
    ctrl.storage.write_meta_list(meta)
    for removed_idx in removed_indices:
        ctrl.storage.delete_layer_property(removed_idx)
        ctrl.storage.delete_mask_property(removed_idx)

    if not meta:
        from ..layer_storage.temp_vg_bridge import delete_temp_vgs, is_wp_session
        if is_wp_session(ctrl.obj):
            delete_temp_vgs(ctrl.obj, layer_idx=index)
            delete_temp_vgs(ctrl.obj, layer_idx=ctrl.active_layer_index)
        ctrl.active_layer_index = -1
        ctrl.finish()
        ctrl.check_for_mask_gaps()
    elif ctrl.active_layer_index in removed_indices:
        switch_to_layer(ctrl, meta[0]["index"])
    else:
        ctrl.finish()
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
        ctrl.storage.delete_mask_property(target_index)
    for idx in others:
        ctrl.storage.delete_layer_property(idx)
        ctrl.storage.delete_mask_property(idx)

    ctrl.storage.write_meta_list(new_meta_list)

    if ctrl.active_layer_index != target_index:
        switch_to_layer(ctrl, target_index)
    else:
        ctrl.finish()
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


def toggle_group_collapsed(ctrl, group_index: int):
    meta = list(ctrl.storage.read_meta_list())
    for l in meta:
        if l.get("index") == group_index and l.get("is_group"):
            l["collapsed"] = not l.get("collapsed", False)
            break
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
        from ..layer_storage.temp_vg_bridge import is_group_layer, is_wp_session

        if is_group_layer(ctrl.storage, index):
            ctrl.obj.superskin_storage.active_is_mask = True

        if is_wp_session(ctrl.obj):
            _switch_wp_layer(ctrl, index)
        else:
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
    from ..layer_storage.temp_vg_bridge import (
        delete_temp_vgs, load_stored_layer_to_temp_vgs, pull_temp_to_storage,
    )

    obj = ctrl.obj
    old_index = ctrl.active_layer_index

    pull_temp_to_storage(obj, ctrl.storage)

    ctrl.active_layer_index = new_index
    ctrl._flatten_to_mesh()
    ctrl._restore_layer_state()

    delete_temp_vgs(obj, layer_idx=old_index)
    load_stored_layer_to_temp_vgs(obj, ctrl.storage, new_index)
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


def get_active_bone_name(ctrl) -> str:
    return ctrl.obj.superskin_storage.active_bone_name


def _set_if_changed(owner, attr, value):
    if getattr(owner, attr) != value:
        setattr(owner, attr, value)


def set_active_bone_name(ctrl, name: str):
    _set_if_changed(ctrl.obj.superskin_storage, "active_bone_name", str(name) if name else "")


def apply_active_bone(ctrl):
    obj = ctrl.obj
    storage = obj.superskin_storage

    try:
        _set_if_changed(ctrl.ctx.scene, "superskin_is_mask_mode", bool(storage.active_is_mask))
    except Exception:
        pass

    if storage.active_is_mask:
        try:
            from ..layer_storage.temp_vg_bridge import mask_vg_name
            _set_if_changed(storage, "last_clicked_index", -1)
            mask_vg = obj.vertex_groups.get(mask_vg_name(ctrl.active_layer_index))
            if mask_vg is not None:
                _set_if_changed(obj.vertex_groups, "active_index", mask_vg.index)
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
        _set_if_changed(storage, "last_clicked_index", vg.index)
    except Exception:
        pass
    try:
        if obj.mode == 'WEIGHT_PAINT':
            from ..layer_storage.temp_vg_bridge import weight_vg_name
            bone_id = None if name.startswith(TEMP_VG_PREFIX) else vg.index
            if bone_id is not None:
                temp_vg = obj.vertex_groups.get(weight_vg_name(ctrl.active_layer_index, bone_id))
                if temp_vg is not None:
                    _set_if_changed(obj.vertex_groups, "active_index", temp_vg.index)
        else:
            _set_if_changed(obj.vertex_groups, "active_index", vg.index)
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
    tag_redraw_areas(window_manager=ctrl.ctx.window_manager)


def get_active_layer_weights_for_display(ctrl) -> dict:
    layer_dict = ctrl.storage.read_active_layer_dict()
    idx_to_name = ctrl.storage.get_local_mapping(ctrl.obj)[1]

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
