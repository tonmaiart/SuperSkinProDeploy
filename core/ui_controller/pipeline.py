"""Pipeline finalisation — reflatten, save/restore, topology heal, context checks.

Every function takes the UIController instance as its first parameter (``ctrl``).
Pure conversion helpers live in core_subsystems/layer_pipeline.py.
"""

import json
import time
import bmesh
from ...core_subsystems.layer_compositor import LayerCompositor
from ...core_subsystems.context_selection_service import ContextSelectionService
from ...core_subsystems.debug_logging import DebugLogService


def finish(ctrl, *, color_only: bool = False, dirty_verts: set = None,
          active_layer_override: dict = None, mask_override: dict = None):
    """Flatten visible layers to the mesh, invalidate caches, and redraw.

    Routes through ``bmesh.from_edit_mesh()`` when the object is in EDIT
    mode so that Vertex Group writes land directly on the edit-bmesh
    without a costly mode round-trip.

    Args:
        color_only: When True, the colour VBO cache is invalidated directly
            and the structural wireframe/point/colour batches are left to
            self-detect staleness via the deform-generation bump below —
            cheaper than a full invalidate, but still correct for weight
            ops that reshape the mesh. Use for weight-paint strokes where
            mesh *topology* (vert/edge/face counts) hasn't changed.
        dirty_verts: forwarded to flatten_to_mesh_edit() (EDIT mode only) to
            restrict the Python-side BMesh scans to a known vertex subset.
            `None` (default) preserves the original full-mesh behavior.
        active_layer_override, mask_override: forwarded to
            flatten_to_mesh_edit() to skip its BMesh read entirely. See that
            function's docstring for the correctness contract.
    """
    in_edit = ctrl.obj.mode == 'EDIT'
    if in_edit:
        flatten_to_mesh_edit(ctrl, dirty_verts=dirty_verts,
                             active_layer_override=active_layer_override,
                             mask_override=mask_override)
    else:
        ctrl.storage.flatten_visible_layers_to_mesh(ctrl.obj)
    ctrl.mesh.update()
    ctrl.obj.update_tag()
    # Real Vertex Group weights just changed, which reshapes the
    # Armature-evaluated mesh at the CURRENT frame — frame_current alone
    # (the visualizer's other staleness signal) can't see that. Bump
    # unconditionally (cheap int increment) so BoneMode/MaskMode's
    # topo/deform cache key self-detects the shape change next draw,
    # even on the color_only fast path.
    ctrl.shader_mgr.bump_deform_generation()
    # Bump an object-level counter so the multi_color_preview draw callback
    # can detect mesh shape changes without importing from core.
    ctrl.obj["__ssp_deform_gen"] = ctrl.obj.get("__ssp_deform_gen", 0) + 1
    if color_only:
        ctrl.shader_mgr.invalidate_color_only()
    else:
        ctrl.shader_mgr.invalidate_and_redraw()
    # In Edit Mode, real VG weights were written to the BMesh. The native
    # Blender weight overlay reads from the BMesh but only refreshes when
    # VIEW_3D areas receive a redraw request — mesh.update() alone is
    # insufficient. Tag all 3D viewports so the overlay updates immediately.
    if in_edit:
        import bpy as _bpy
        for window in _bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()


def flatten_to_mesh(ctrl):
    ctrl.storage.flatten_visible_layers_to_mesh(ctrl.obj)


def flatten_to_mesh_edit(ctrl, dirty_verts: set = None, *,
                         active_layer_override: dict = None,
                         mask_override: dict = None):
    """Write composited layer weights directly through the edit-bmesh.

    In Edit Mode, the active layer lives in temp VGs (__ssp_*).
    Other layers still live in ss_layer_N.
    This function composites all of them correctly.

    Args:
        dirty_verts: when given, restricts the "old_state" pre-write BMesh
            scan and the final real-VG write loop to only these vertex
            indices, instead of the whole mesh -- for hot paths that know
            exactly which vertices could possibly have changed since the
            last call (e.g. a modal weight-painting gesture). This assumes
            the compositor's per-vertex output is independent across
            vertices, so any vertex NOT in `dirty_verts` has an unchanged
            result and can be safely skipped. `None` (default) preserves the
            original full-mesh behavior exactly.

            IMPORTANT: this must NOT be used to restrict the
            `read_temp_vgs_from_bm()` call below that builds the active
            layer's *compositor input* -- that read must always cover the
            full active layer, or vertices outside `dirty_verts` would look
            like they have zero active-layer weight to the compositor and
            get silently zeroed in the output. Only the two loops *after*
            the compositor call (which deal with the real VG *write*, not
            read) are restricted.

            NOTE: this does NOT reduce the cost of the
            `LayerCompositor.composite_layers()` call itself -- that Rust
            call is unconditionally O(all_visible_layers x num_verts)
            regardless of this parameter, since its FFI signature has no
            vertex-subset input today.
        active_layer_override: when given, SKIPS the `read_temp_vgs_from_bm()`
            BMesh scan below entirely and uses this dict directly as the
            active layer's compositor input instead
            ({v_idx: {bone_name: weight}}, string bone-keyed -- same shape
            `read_temp_vgs_from_bm()` normally returns). Safe ONLY when the
            caller can guarantee this dict is already an exact, complete,
            up-to-date picture of the active layer's temp-VG state (e.g. a
            modal gesture that holds Blender's modal lock for its whole
            duration, so nothing else could have mutated the temp VGs, and
            already has this data in memory from its own Rust computation --
            see features/weight_apply/weight_apply_feature.py::apply_action()).
            `None` (default) preserves the original BMesh-read behavior.
        mask_override: companion to `active_layer_override` for the active
            layer's mask ({v_idx: float}). Only consulted when
            `active_layer_override` is given. `None`/omitted there is
            treated as "no mask data" for the active layer, same as
            `read_temp_vgs_from_bm()` returning an empty mask_dict.
    """
    from ..layer_storage.temp_vg_bridge import (
        has_temp_vgs, read_temp_vgs_from_bm
    )

    mesh = ctrl.mesh
    if "ss_layers_meta" not in mesh:
        return

    vg_list = ctrl.obj.vertex_groups
    num_verts = len(mesh.vertices)
    if num_verts == 0 or len(vg_list) == 0:
        return

    _profile = DebugLogService.is_enabled("core_pipeline")
    _t0 = time.perf_counter() if _profile else None

    meta = json.loads(mesh.get("ss_layers_meta", "[]"))
    idx_to_name = {vg.index: vg.name for vg in vg_list
                   if not vg.name.startswith("__ssp_")}

    active_idx = ctrl.storage.get_active_layer_index()

    layer_data_map = {}
    mask_data_map = {}

    for layer in meta:
        l_idx = layer["index"]
        if l_idx == active_idx:
            continue
        raw = mesh.get(f"ss_layer_{l_idx}")
        if raw:
            layer_data_map[l_idx] = raw
        raw_mask = mesh.get(f"ss_mask_{l_idx}")
        if raw_mask:
            mask_data_map[l_idx] = raw_mask

    if has_temp_vgs(ctrl.obj):
        if active_layer_override is not None:
            # Caller already has the complete, current active-layer dict in
            # memory (see docstring) -- skip the BMesh read entirely instead
            # of redundantly re-deriving the exact same data via a
            # per-vertex bv[deform] scan (a Blender-Python/C boundary call,
            # not a cheap dict lookup, so this scales with total mesh vertex
            # count on every tick regardless of brush size).
            layer_dict = active_layer_override
            mask_dict = mask_override or {}
        else:
            # Read directly from the active edit BMesh's deform layer.
            # update_from_editmode() does not reliably sync VG weights to
            # mesh.vertices during Edit Mode operations, so reading from the
            # BMesh directly guarantees that weight ops (which write via
            # write_layer_to_temp_vgs_bm) are visible here immediately.
            # Always a full read -- this becomes the compositor's active-layer
            # input, which must represent every vertex with real weight, not
            # just the vertices this tick's `dirty_verts` names (see docstring).
            bm_active = bmesh.from_edit_mesh(mesh)
            layer_dict, mask_dict, _ = read_temp_vgs_from_bm(bm_active, ctrl.obj)
        # Pass the dict straight through instead of encode()-ing it into a
        # pickled/zlib/base64 blob -- _composite_layers()'s harvest loop
        # decodes whatever lands in layer_data_map/mask_data_map right back
        # into a dict a few lines later regardless of source, so encoding
        # here was a pure round-trip that pickled+compressed the ENTIRE
        # active layer every single tick just to immediately undo it.
        # LayerCompositor.composite_layers() (via codec.py) accepts a raw
        # dict value transparently alongside the encoded-string entries the
        # other (non-active, storage-backed) layers still use.
        layer_data_map[active_idx] = layer_dict
        if mask_dict:
            mask_data_map[active_idx] = mask_dict
    else:
        raw = mesh.get(f"ss_layer_{active_idx}")
        if raw:
            layer_data_map[active_idx] = raw
        raw_mask = mesh.get(f"ss_mask_{active_idx}")
        if raw_mask:
            mask_data_map[active_idx] = raw_mask

    if _profile:
        _t_setup = time.perf_counter()

    # Activated: rust_logic.so has been rebuilt with dirty_verts support in
    # rust_composite_layers (verified -- accepts and correctly handles the
    # 5th positional arg). This is what turns the compositor's O(num_verts)
    # cost into O(len(dirty_verts)) on the weight-apply gesture hot path.
    result = LayerCompositor.composite_layers(meta, layer_data_map, mask_data_map,
                                               idx_to_name, num_verts, dirty_verts=dirty_verts)

    if _profile:
        _t_composite = time.perf_counter()

    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    deform = bm.verts.layers.deform.verify()

    name_to_idx = {vg.name: vg.index for vg in vg_list
                   if not vg.name.startswith("__ssp_")}

    old_state: dict = {}
    verts_to_scan = (bm.verts[i] for i in dirty_verts) if dirty_verts is not None else bm.verts
    for bv in verts_to_scan:
        v_deform = bv[deform]
        if v_deform:
            filtered = {g_idx: w for g_idx, w in v_deform.items()
                        if g_idx in idx_to_name}
            if filtered:
                old_state[bv.index] = filtered

    new_state = LayerCompositor.bone_weights_to_deform_state(result, name_to_idx)

    if _profile:
        _t_oldstate = time.perf_counter()

    # When dirty_verts is given, old_state was only scanned for those verts,
    # and every vertex outside dirty_verts is assumed unchanged (compositor
    # output is per-vertex independent) -- so the write loop below only
    # needs to consider dirty_verts, not the full old_state|new_state union.
    all_affected = dirty_verts if dirty_verts is not None else (set(old_state.keys()) | set(new_state.keys()))
    for v_idx in all_affected:
        old = old_state.get(v_idx, {})
        new = new_state.get(v_idx, {})
        if old == new:
            continue
        bv = bm.verts[v_idx]
        v_deform = bv[deform]
        for g_idx in list(v_deform.keys()):
            if g_idx in idx_to_name and g_idx not in new:
                del v_deform[g_idx]
        for g_idx, w in new.items():
            v_deform[g_idx] = w

    if _profile:
        _t_bmwrite = time.perf_counter()

    bmesh.update_edit_mesh(mesh)

    if _profile:
        _t_updatemesh = time.perf_counter()
        DebugLogService.log(
            "core_pipeline",
            f"flatten_to_mesh_edit() breakdown: "
            f"setup={1000 * (_t_setup - _t0):.2f}ms "
            f"composite_layers={1000 * (_t_composite - _t_setup):.2f}ms "
            f"old_state_scan={1000 * (_t_oldstate - _t_composite):.2f}ms "
            f"bmesh_write={1000 * (_t_bmwrite - _t_oldstate):.2f}ms "
            f"update_edit_mesh={1000 * (_t_updatemesh - _t_bmwrite):.2f}ms "
            f"total={1000 * (_t_updatemesh - _t0):.2f}ms",
        )


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

    ctrl.obj.superskin_storage.active_is_mask = False

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
