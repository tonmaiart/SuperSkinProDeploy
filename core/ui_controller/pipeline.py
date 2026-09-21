"""Pipeline finalisation — reflatten, save/restore, topology heal, context checks.

Every function takes the UIController instance as its first parameter (``ctrl``).
Pure conversion helpers live in core_subsystems/layer_pipeline.py.
"""

from ...core_subsystems.context_selection_service import ContextSelectionService


def finish(ctrl, *, color_only: bool = False, dirty_verts: set = None,
          active_layer_override: dict = None, mask_override: dict = None):
    """Flatten visible layers to the mesh's real deform vertex groups,
    invalidate caches, and redraw.

    dirty_verts / active_layer_override / mask_override are accepted for
    caller compatibility and ignored: the flatten always recomposites the
    whole mesh.

    color_only: when True only the colour VBO cache is invalidated; the
    structural batches self-detect staleness via the deform-generation bump.
    """
    ctrl.storage.flatten_visible_layers_to_mesh(ctrl.obj)
    ctrl.mesh.update()
    ctrl.obj.update_tag()
    ctrl.shader_mgr.bump_deform_generation()
    ctrl.obj["__ssp_deform_gen"] = ctrl.obj.get("__ssp_deform_gen", 0) + 1
    if color_only:
        ctrl.shader_mgr.invalidate_color_only()
    else:
        ctrl.shader_mgr.invalidate_and_redraw()
    import bpy as _bpy
    for window in _bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def flatten_to_mesh(ctrl):
    ctrl.storage.flatten_visible_layers_to_mesh(ctrl.obj)


def save_current_layer_state(ctrl):
    idx = ctrl.active_layer_index
    meta = ctrl.storage.read_meta_list()

    sel = getattr(ctrl.obj.superskin_storage, "selected_names", ",")
    meta = ctrl._layer_mgr.set_selected_bones(meta, idx, sel)

    active_idx = ctrl.obj.superskin_storage.last_clicked_index
    active_name = ""
    if 0 <= active_idx < len(ctrl.obj.vertex_groups):
        active_name = ctrl.obj.vertex_groups[active_idx].name
    meta = ctrl._layer_mgr.set_active_bone_name(meta, idx, active_name)
    ctrl.storage.write_meta_list(meta)


def restore_layer_state(ctrl):
    meta = ctrl.storage.read_meta_list()
    idx = ctrl.active_layer_index

    locks = ctrl._layer_mgr.get_bone_locks(meta, idx)
    for item in ctrl.obj.superskin_bones_collection:
        item.lock_weight = locks.get(item.name, False)

    sel = ctrl._layer_mgr.get_selected_bones(meta, idx)
    if sel and hasattr(ctrl.obj, "superskin_storage"):
        ctrl.obj.superskin_storage.selected_names = sel

    # active_is_mask is intentionally left untouched here so the current
    # Weight/Mask sub-mode carries over across a layer switch.

    name = ctrl._layer_mgr.get_active_bone_name(meta, idx)
    if name and name in ctrl.obj.vertex_groups:
        ctrl.obj.superskin_storage.last_clicked_index = ctrl.obj.vertex_groups[name].index


def is_mask_context(ctrl) -> bool:
    try:
        return ContextSelectionService.is_mask_context(ctrl.ctx.scene)
    except Exception:
        return False


def heal_topology_if_needed(ctrl) -> bool:
    """Auto-heal layer/mask storage after topology edits, reflattening
    if anything changed. Returns True if healing did anything."""
    healed = ctrl.storage.heal_new_vertices(ctrl.obj)
    if healed:
        ctrl.storage.flatten_visible_layers_to_mesh(ctrl.obj)
        ctrl.mesh.update()
        ctrl.obj.update_tag()
    return healed
