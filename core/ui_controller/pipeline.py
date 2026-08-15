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
from ...core_subsystems.profiler import ProfilerService


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
                         mask_override: dict = None,
                         temp_vg_new_weights: dict = None,
                         temp_vg_all_ssp_indices: set = None):
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

            NOTE: this DOES also reduce the cost of the
            `LayerCompositor.composite_layers()` call itself -- `dirty_verts`
            is forwarded through to the Rust `rust_composite_layers`/
            `rust_composite_layers_mixed` call below (rust_logic has been
            rebuilt with this support, see the call site further down), so
            the per-vertex compositor cost scales with O(len(dirty_verts))
            rather than O(num_verts). It still scales with the number of
            visible layers regardless of this parameter -- a mesh with many
            visible layers stays proportionally expensive no matter how
            small the brush.
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
        temp_vg_new_weights, temp_vg_all_ssp_indices: when BOTH are given
            (together with `dirty_verts`, required for this path), this
            function folds the __ssp_* temp-VG write that would otherwise be
            a separate `write_layer_to_temp_vgs_bm()` call into its own
            per-vertex loop below, instead of running as its own separate
            `bm.verts` scan first. See `core/layer_storage/
            temp_vg_bridge.py`'s `prepare_temp_vg_write()` for how to compute
            these two values (it returns exactly this pair, computed without
            touching the BMesh) and `core/facade/write.py`'s
            `write_active_layer_from_calc()` for the canonical caller.

            Safe because the two write targets never share a key within a
            vertex's BMesh deform dict: `temp_vg_all_ssp_indices` only ever
            contains __ssp_* VG indices, `idx_to_name` (built below, used for
            the real-VG old/new diff) only ever contains non-__ssp_* indices
            -- see `prepare_temp_vg_write()`'s own docstring for why. Reading
            the real-VG "old" state AFTER applying the temp-VG write (rather
            than before, as the non-merged path does via its separate
            `write_layer_to_temp_vgs_bm()` call preceding this function) is
            still correct because that write can only ever touch a __ssp_*
            key, never one `idx_to_name` recognizes, so it cannot change what
            the "old" read observes.

            `None` (default) for either preserves the original two-separate-
            calls behavior exactly -- every caller except
            `write_active_layer_from_calc()` is unaffected.
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

    # Combined gate: either consumer wanting timestamps is enough reason to
    # compute them (time.perf_counter() itself is cheap) -- this keeps
    # _t_setup/_t_composite/etc. always defined below whenever EITHER
    # DebugLogService's "core_pipeline" category or ProfilerService is on,
    # instead of silently NameError-ing if only one of the two is enabled.
    _profile_debug = DebugLogService.is_enabled("core_pipeline")
    _profile_metrics = ProfilerService.is_enabled()
    _profile = _profile_debug or _profile_metrics
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

    new_state = LayerCompositor.bone_weights_to_deform_state(result, name_to_idx)

    _merged_temp_vg = dirty_verts is not None and temp_vg_new_weights is not None

    if _merged_temp_vg:
        # Single combined pass: temp-VG write + real-VG old-state read +
        # real-VG new-state write, all within one bm.verts[v_idx] visit per
        # dirty vertex. See this function's docstring for why interleaving
        # these three steps (instead of three separate bm.verts scans, one
        # of them in a wholly separate function) is safe.
        if _profile:
            _t_oldstate = time.perf_counter()

        for v_idx in dirty_verts:
            bv = bm.verts[v_idx]
            v_deform = bv[deform]

            new_temp = temp_vg_new_weights.get(v_idx, {})
            for gi in list(v_deform.keys()):
                if gi in temp_vg_all_ssp_indices and gi not in new_temp:
                    del v_deform[gi]
            for gi, w in new_temp.items():
                v_deform[gi] = w

            old = {g_idx: w for g_idx, w in v_deform.items() if g_idx in idx_to_name}
            new = new_state.get(v_idx, {})
            if old == new:
                continue
            for g_idx in list(v_deform.keys()):
                if g_idx in idx_to_name and g_idx not in new:
                    del v_deform[g_idx]
            for g_idx, w in new.items():
                v_deform[g_idx] = w

        if _profile:
            _t_bmwrite = time.perf_counter()
    else:
        old_state: dict = {}
        verts_to_scan = (bm.verts[i] for i in dirty_verts) if dirty_verts is not None else bm.verts
        for bv in verts_to_scan:
            v_deform = bv[deform]
            if v_deform:
                filtered = {g_idx: w for g_idx, w in v_deform.items()
                            if g_idx in idx_to_name}
                if filtered:
                    old_state[bv.index] = filtered

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
        if _profile_metrics:
            # Gated on _profile_metrics (the real ProfilerService toggle), not
            # _profile_debug -- DebugLogService.is_enabled() now always
            # returns True unconditionally (see its own docstring: capture is
            # always-on so the Debug Console buffer stays complete regardless
            # of which category checkboxes are ticked), so _profile_debug can
            # no longer distinguish "user wants this" from "always". Gating
            # this specific block on it made the f-string format + print()
            # syscall below fire on EVERY flatten_to_mesh_edit() call in
            # production, permanently, landing entirely in the untracked gap
            # between this function's own "total" (_t_updatemesh - _t0,
            # ending right above) and the caller's outer timer around this
            # whole call -- see superskinpro-core-optimize skill session notes.
            # When _merged_temp_vg is True, old_state_scan/bmesh_write no
            # longer measure two separate bm.verts passes -- they measure
            # (near-zero) / (one combined pass that also includes the
            # __ssp_* temp-VG write that would otherwise be a separate
            # write_layer_to_temp_vgs_bm() call's own scan). A near-zero
            # old_state_scan here is expected, not a bug -- see this
            # function's docstring for temp_vg_new_weights/
            # temp_vg_all_ssp_indices.
            _merge_note = " [merged temp-VG pass]" if _merged_temp_vg else ""
            DebugLogService.log(
                "core_pipeline",
                f"flatten_to_mesh_edit() breakdown{_merge_note}: "
                f"setup={1000 * (_t_setup - _t0):.2f}ms "
                f"composite_layers={1000 * (_t_composite - _t_setup):.2f}ms "
                f"old_state_scan={1000 * (_t_oldstate - _t_composite):.2f}ms "
                f"bmesh_write={1000 * (_t_bmwrite - _t_oldstate):.2f}ms "
                f"update_edit_mesh={1000 * (_t_updatemesh - _t_bmwrite):.2f}ms "
                f"total={1000 * (_t_updatemesh - _t0):.2f}ms",
            )
            _base = "core.pipeline.flatten_to_mesh_edit"
            # Size hint = the vertex count this call actually scoped its work
            # to (dirty_verts when given, else the whole mesh) -- lets a
            # reader confirm cost tracks this number rather than staying
            # flat regardless of brush size.
            _size = len(dirty_verts) if dirty_verts is not None else num_verts
            ProfilerService.record(f"{_base}.setup", 1000 * (_t_setup - _t0), _size)
            ProfilerService.record(f"{_base}.composite_layers", 1000 * (_t_composite - _t_setup), _size)
            ProfilerService.record(f"{_base}.old_state_scan", 1000 * (_t_oldstate - _t_composite), _size)
            ProfilerService.record(f"{_base}.bmesh_write", 1000 * (_t_bmwrite - _t_oldstate), _size)
            ProfilerService.record(f"{_base}.update_edit_mesh", 1000 * (_t_updatemesh - _t_bmwrite), _size)
            ProfilerService.record(f"{_base}.total", 1000 * (_t_updatemesh - _t0), _size)


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
