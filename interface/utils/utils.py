"""UI Utility Garbage Collector & Shared Logic for SuperSkinPro.

Consolidated from:
  - utils.py (force_open/force_close)
  - ui_influence_list.py (wildcard, depth caches, display order, influence scanner, visual order)
  - ui_layer_list.py (layer target resolver, visualizer enforcer, sync_layers_to_ui_collection)
  - widget_layers.py (depsgraph / load handlers)
"""

import bpy
import json
import time
import traceback

from ... import ADDON_NAME
from ...core.facade import CoreFacade
from ...core_subsystems.topology_cache_manager import TopologyCacheManager
from ...core.layer_storage.storage_service import LayerStorageService
from ...core_subsystems.rust_weight_engine import RustWeightEngine
from ...core.bone_identity import BoneIdentityService
from ...core_subsystems.debug_logging import DebugLogService
# LayerCompositor kept function-scoped in sync_bones_to_ui_collection —
# hoisting it triggers a circular import through core_subsystems → features → ui.utils.


# ═══════════════════════════════════════════════════════════════════════════
#  Guard helpers — shared validity checks reused across operators/ops_*.py
# ═══════════════════════════════════════════════════════════════════════════


def _is_valid_mesh(obj):
    """True if *obj* is a usable mesh object."""
    return bool(obj) and obj.type == 'MESH'


def _has_layer_system(obj):
    """True if *obj* is a valid mesh with an initialised layer system."""
    return _is_valid_mesh(obj) and "ss_layers_meta" in obj.data


# ═══════════════════════════════════════════════════════════════════════════
#  Tab / panel helpers (from original utils.py)
# ═══════════════════════════════════════════════════════════════════════════


def exit_mask_mode_if_active(context, obj):
    """If mask mode is active (superskin_is_mask_mode flag), exit it.

    Saves the current mode, switches to OBJECT if needed,
    exits the mask editing context, clears flags,
    restores the visualizer, and returns to the previous mode.

    Only triggers when ``superskin_is_mask_mode`` is explicitly True.

    Suppresses the auto-close-panel-on-mode-exit side effect for the
    duration of the round-trip so callers that are *not* already wrapped
    by ``_run_in_object_context`` (e.g. ``BoneListAdapter.on_single_select``) don't trigger a spurious panel close.

    Returns True if mask mode was active and exited, False otherwise.
    """
    in_mask = context.scene.superskin_is_mask_mode
    if not in_mask:
        return False

    ctrl = CoreFacade(context).get_ctrl()
    prev_mode = obj.mode if obj else 'OBJECT'
    was_suppressing = context.scene.superskin_internal_transaction
    context.scene.superskin_internal_transaction = True
    try:
        if prev_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        context.scene.superskin_is_mask_mode = False
        active_vg_idx = obj.vertex_groups.active_index
        if active_vg_idx >= 0:
            ctrl.exit_mask_editing_context(active_vg_idx)
        ctrl.restore_visualizer_from_mask()
        if prev_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=prev_mode)
    finally:
        context.scene.superskin_internal_transaction = was_suppressing
    return True


def force_open_super_skin_tab():
    """Open the 3D View sidebar and switch to the SuperSkinPro panel."""
    area = next((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), None)
    if not area:
        return

    # Force the sidebar (N-panel) open if it's collapsed
    if not area.spaces.active.show_region_ui:
        area.spaces.active.show_region_ui = True

    # Switch the N-panel category to SuperSkinPro (ADDON_NAME -- must match
    # panel_main.py's VIEW3D_PT_mw_master_modular_panel.bl_category exactly)
    ui_region = next((r for r in area.regions if r.type == 'UI'), None)
    if ui_region:
        with bpy.context.temp_override(area=area, region=ui_region):
            ui_region.active_panel_category = ADDON_NAME
            ui_region.tag_redraw()


def force_close_tab():
    """Collapse the 3D View sidebar only if ADDON_NAME is the active panel category."""
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            ui_region = next((r for r in area.regions if r.type == 'UI'), None)
            if ui_region and ui_region.active_panel_category == ADDON_NAME:
                with bpy.context.temp_override(area=area):
                    area.spaces.active.show_region_ui = False


def is_super_skin_tab_open():
    """Return True if the 3D View sidebar is open and its active panel
    category is ADDON_NAME. Used to make "Popup Main Panel" a real
    toggle (open <-> collapse) instead of an idempotent open-only action."""
    area = next((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), None)
    if not area or not area.spaces.active.show_region_ui:
        return False
    ui_region = next((r for r in area.regions if r.type == 'UI'), None)
    return bool(ui_region and ui_region.active_panel_category == ADDON_NAME)


def _run_in_object_context(context, callback, *args):
    """Execute *callback(*args)* in OBJECT mode, restoring the previous mode.

    Many layer-management operations require the mesh to be in OBJECT mode.
    This helper encapsulates the save → switch → execute → restore pattern
    so that operators can stay concise.

    Suppresses the auto-close-panel-on-mode-exit side effect for the
    duration of the round-trip.  Returns whatever *callback* returns.
    """
    obj = context.active_object
    prev_mode = obj.mode
    was_suppressing = context.scene.superskin_internal_transaction
    context.scene.superskin_internal_transaction = True
    try:
        if prev_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        result = callback(*args)
        if prev_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=prev_mode)
        return result
    finally:
        context.scene.superskin_internal_transaction = was_suppressing


# ═══════════════════════════════════════════════════════════════════════════
#  Performance caches (from ui_influence_list.py)
# ═══════════════════════════════════════════════════════════════════════════

# ⚡ Performance cache: bone display order — invalidated when armature/mesh changes
_display_order_cache = {}
_display_order_cache_key = None  # (mesh_name, arm_name, frozenset(deform_bone_names))

# ⚡ Performance cache: influence scanner visible bones — invalidated on layer/blob change
_influence_visible_cache = {}
_influence_visible_cache_key = None  # (id(mesh_data), active_layer_idx, blob_hash)

# Debounce window for the cache above: both its EDIT_TEMP key (ShaderManager
# deform-generation counter) and its Object-Mode key (ss_layer_N blob hash)
# change on every single completed weight write — including every applied
# tick of a continuous weight-apply gesture, which can fire dozens of times
# per second. A brief staleness window here is imperceptible for a
# bone-influence display list and avoids a full mesh rescan (BMesh read +
# int-cast + Rust call) on every gesture tick.
_influence_visible_last_recompute_wall = 0.0
_INFLUENCE_VISIBLE_DEBOUNCE_SECONDS = 0.2


def _get_cached_display_order(arm_obj, deform_bones):
    """⚡ Retain cache mechanism exclusively for bone item sequencing order.

    Hoisted import: TopologyCacheManager (was function-scoped at line 183).
    """
    global _display_order_cache, _display_order_cache_key

    mesh_name = ""
    arm_name = ""
    if arm_obj:
        arm_name = arm_obj.name
        if arm_obj.data:
            mesh_name = getattr(arm_obj.data, 'name', '')

    cache_key = (mesh_name, arm_name, frozenset(deform_bones))

    if _display_order_cache_key == cache_key and _display_order_cache:
        return _display_order_cache

    try:
        _display_order_cache = TopologyCacheManager.compute_bone_display_order(arm_obj, deform_bones)
    except Exception as e:
        print(f"[SuperSkinPro Error] Failed computing display_order: {e}")
        _display_order_cache = list(deform_bones)

    _display_order_cache_key = cache_key
    return _display_order_cache


def _get_visible_influence_bones(context, data):
    """⚡ Pure deterministic weight scanner cache. Stable during selection changes.

    In Edit Mode with temp VGs loaded, the active layer's live weights sit
    in the __ssp_* BMesh channels and haven't been baked back to ss_layer_N
    yet (see docs on the 0016 undo redesign) — reading ss_layer_N here would
    silently show stale/empty data while painting. Mirrors the same
    mode-aware routing as core/facade/read.py's _read_active_layer_int().

    The ss_layer_N blob hash used to key the cache in Object Mode doesn't
    move at all during an Edit-Mode paint stroke (nothing is baked back
    until layer switch/mode exit), so it can't be reused as-is here. Instead
    this branch keys on ShaderManager.get_deform_generation() — the same
    process-wide counter core/facade/write.py's finish() and
    write_active_layer_from_calc() already bump on every completed weight
    write, and only on that (not on mouse-move/redraw) — so draw() calls
    between strokes still hit the cache instead of re-running the Rust
    scanner on every redraw tick.

    Hoisted imports: LayerStorageService, RustWeightEngine (were function-scoped,
    absolute 'SuperSkinPro' references at lines 221-222).
    """
    global _influence_visible_cache, _influence_visible_cache_key
    global _influence_visible_last_recompute_wall

    from ...core.layer_storage.temp_vg_bridge import has_temp_vgs, read_temp_vgs_from_bm
    from ...core.shaders.shader_manager import ShaderManager

    mesh_data = data.data
    in_edit_temp = data.mode == 'EDIT' and has_temp_vgs(data)

    if in_edit_temp:
        cache_key = (id(mesh_data), 'EDIT_TEMP', ShaderManager.get_deform_generation())
    else:
        active_layer_idx = mesh_data.get("ss_active_layer", 0)
        raw_blob = mesh_data.get(f"ss_layer_{active_layer_idx}", "")
        cache_key = (id(mesh_data), active_layer_idx, hash(raw_blob))

    if _influence_visible_cache_key == cache_key:
        return set(_influence_visible_cache)

    now = time.monotonic()
    if (now - _influence_visible_last_recompute_wall) < _INFLUENCE_VISIBLE_DEBOUNCE_SECONDS:
        # cache_key changed but the cache was refreshed only moments ago --
        # deliberately leave _influence_visible_cache_key untouched (still
        # the OLD key) so the very next call, once the debounce window has
        # elapsed (e.g. right after a gesture releases), sees a mismatch
        # again and performs one final, fully correct recompute.
        return set(_influence_visible_cache)

    storage = LayerStorageService(mesh_data)

    if in_edit_temp:
        import bmesh as _bm_mod
        bm = _bm_mod.from_edit_mesh(mesh_data)
        raw_layer_dict, _, _ = read_temp_vgs_from_bm(bm, data)
    else:
        raw_layer_dict = storage.read_active_layer_dict()

    bone_to_id, id_to_bone = storage.get_local_mapping(data)

    layer_int: dict[int, dict[int, float]] = {}
    for v_idx, weights in raw_layer_dict.items():
        v_int = int(v_idx)
        inner = {}
        if isinstance(weights, dict):
            for b_name, w in weights.items():
                b_id = bone_to_id.get(b_name)
                if b_id is not None:
                    inner[b_id] = float(w)
        layer_int[v_int] = inner

    rust = RustWeightEngine("influence_scanner")
    rust_set = rust.call("rust_get_visible_influence_bones", layer_int)

    visible_bones = set()
    if rust_set:
        for b_id in rust_set:
            b_name = id_to_bone.get(b_id)
            if b_name:
                visible_bones.add(b_name)

    DebugLogService.log(
        "core_pipeline",
        f"_get_visible_influence_bones() recompute: obj={data.name!r} "
        f"in_edit_temp={in_edit_temp} raw_layer_dict verts={len(raw_layer_dict)} "
        f"rust_set={rust_set!r} visible_bones={sorted(visible_bones)!r} "
        f"obj.mode={getattr(data, 'mode', '?')}",
    )

    _influence_visible_cache = frozenset(visible_bones)
    _influence_visible_cache_key = cache_key
    _influence_visible_last_recompute_wall = now
    return set(visible_bones)


def _get_display_order_impl(context, data):
    """Hierarchy‑ordered ``vertex_groups`` indices (deform bones only; a
    vertex group whose name isn't a current deform bone is dropped from
    the order entirely — this is the Deform Bones list, not a generic
    vertex-group list). Used only by ``sync_bones_to_ui_collection`` now
    (moved out of ``widget_deform_bones.py`` during the bones-list mirror-
    collection refactor to avoid a circular import — that module imports
    from this one already, and the sync function below needs this same
    ordering at write time instead of draw time).

    Hoisted import: traceback (was function-scoped at line 263).
    """
    items = data.vertex_groups
    arm_obj = next((m.object for m in data.modifiers
                    if m.type == 'ARMATURE' and m.object), None)
    deform_bones = set()
    if arm_obj:
        deform_bones = {b.name for b in arm_obj.data.bones if b.use_deform}

    # Counts + the two mismatch sets only -- not the full deform_bones/
    # vg_names lists. Those two lists mostly overlap on every real rig, so
    # dumping both in full repeats the same ~60+ names on every single
    # depsgraph tick for no diagnostic gain; the mismatch sets are what
    # actually answers "is a VG missing / is an extra one hanging around"
    # and are normally empty or tiny.
    if DebugLogService.is_enabled("bone_id"):
        vg_names_set = {vg.name for vg in items}
        DebugLogService.log(
            "bone_id",
            f"_get_display_order_impl(): arm_obj={arm_obj.name if arm_obj else None!r} "
            f"deform_bones={len(deform_bones)} vg_names={len(vg_names_set)} "
            f"vg_only={sorted(vg_names_set - deform_bones)!r} "
            f"deform_only={sorted(deform_bones - vg_names_set)!r}",
        )

    if arm_obj and deform_bones:
        try:
            ordered_names = _get_cached_display_order(arm_obj, deform_bones)
        except Exception:
            traceback.print_exc()
            return list(range(len(items)))

        name_to_idx = {item.name: i for i, item in enumerate(items)}
        result = []
        for name in ordered_names:
            idx = name_to_idx.get(name)
            if idx is not None:
                result.append(idx)

        DebugLogService.log(
            "bone_id",
            f"_get_display_order_impl(): ordered_names_count={len(ordered_names)} "
            f"result_indices_count={len(result)} dropped={len(ordered_names) - len(result)}",
        )
        return result
    else:
        return list(range(len(items)))


def sync_bones_to_ui_collection(obj):
    """Mirror the Deform Bones list's paintable rows (real vertex groups,
    hierarchy-ordered) plus orphaned-weight pseudo-rows (see
    ``core.bone_identity``) into ``obj.superskin_bones_collection`` — the
    real ``bpy_prop_collection`` the unified ``template_list`` widget binds
    to. Mirrors ``sync_layers_to_ui_collection`` above for the same reason:
    Blender's ``template_list`` can only iterate a real RNA collection, and
    orphan rows by definition have no backing ``VertexGroup``.

    Write-only context (depsgraph handler / operator) — never call from
    draw(), same constraint as ``BoneIdentityService.backfill_and_scan()``.
    Mirrors *every* paintable/orphan row unfiltered; ``bone_list_filter_mode``
    stays a draw-time concern handled by the UIList's ``filter_items``,
    exactly as it did against the real ``vertex_groups`` collection before
    this refactor — baking it in here would mean switching the filter mode
    wouldn't visibly update the list until the next depsgraph tick.

    Hoisted imports: BoneIdentityService, LayerStorageService, LayerCompositor
    (were function-scoped, absolute 'SuperSkinPro' references at lines 309, 329-330).
    """
    if not obj or obj.type != 'MESH':
        return

    DebugLogService.log("bone_id", f"sync_bones_to_ui_collection() ENTRY: obj={obj.name!r}")

    order = _get_display_order_impl(None, obj)
    vg_list = obj.vertex_groups
    orphans = BoneIdentityService.get_scan_for_object(obj)

    # Re-derive the native template_list highlight from the real "active
    # bone" source of truth on every rebuild (last_clicked_index /
    # active_orphan_name) — col.clear()+add() below reassigns every row's
    # position, so superskin_bones_idx must be recomputed each time too, or
    # it silently points at whatever bone now happens to sit at the old
    # integer position. Mirrors how sync_layers_to_ui_collection re-derives
    # superskin_layers_idx from ss_active_layer on every rebuild.
    storage = obj.superskin_storage
    active_orphan = storage.active_orphan_name
    active_vg_name = None
    if (not active_orphan and not storage.active_is_mask
            and 0 <= storage.last_clicked_index < len(vg_list)):
        active_vg_name = vg_list[storage.last_clicked_index].name

    try:
        from ...core_subsystems.layer_compositor import LayerCompositor
        _sto = LayerStorageService(obj.data)
        _meta = _sto.read_meta_list()
        locks = LayerCompositor.get_bone_locks(_meta, _sto.get_active_layer_index())
    except Exception:
        locks = {}

    col = obj.superskin_bones_collection
    col.clear()

    mask_item = col.add()
    mask_item.name = "Mask"
    mask_item.vg_index = -1
    mask_item.is_orphan = False
    mask_item.is_mask = True
    if storage.active_is_mask:
        obj.superskin_bones_idx = len(col) - 1

    DebugLogService.log(
        "bone_id",
        f"sync_bones_to_ui_collection(): mask row added at col index 0, "
        f"is_mask={mask_item.is_mask} active_is_mask={storage.active_is_mask}",
    )

    for vg_idx in order:
        if vg_idx < 0 or vg_idx >= len(vg_list):
            continue
        vg = vg_list[vg_idx]
        item = col.add()
        item.name = vg.name
        item.vg_index = vg_idx
        item.is_orphan = False
        item.lock_weight = locks.get(vg.name, False)
        if active_vg_name and vg.name == active_vg_name:
            obj.superskin_bones_idx = len(col) - 1

    for orphan in orphans:
        item = col.add()
        item.name = orphan["name"]
        item.vg_index = -1
        item.is_orphan = True
        item.lock_weight = locks.get(orphan["name"], False)
        item.classification = orphan.get("classification", "")
        item.suggested_target = orphan.get("suggested_target") or ""
        if active_orphan and orphan["name"] == active_orphan:
            obj.superskin_bones_idx = len(col) - 1

    DebugLogService.log(
        "bone_id",
        f"sync_bones_to_ui_collection() EXIT: {len(col)} total rows "
        f"({len(order)} bone, {len(orphans)} orphan, 1 mask), "
        f"superskin_bones_idx={obj.superskin_bones_idx}",
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Layer helpers (from ui_layer_list.py)
# ═══════════════════════════════════════════════════════════════════════════


def _resolve_layer_target(obj, layer_index_prop):
    """Return the slot index of the layer the user is operating on.

    Prefers the explicit *layer_index_prop* when set (≥0); otherwise
    reads the currently highlighted item from the UI collection.

    Args:
        obj: The active mesh object.
        layer_index_prop: Value of the operator's ``layer_index`` property.

    Returns:
        ``int`` — the layer slot index, or ``-1`` if unresolvable.
    """
    if layer_index_prop >= 0:
        return layer_index_prop
    col = obj.superskin_layers_collection
    idx = obj.superskin_layers_idx
    if 0 <= idx < len(col):
        return col[idx].index
    return -1


def _select_only_layer(obj, slot_index: int):
    """Force the layer multi-select pool to contain only *slot_index*.

    Layer CRUD operators (add/remove/move/duplicate) change which layer is
    active without going through LayerListAdapter.write_selection() — the
    only other place that writes layer_selected_indices /
    layer_selection_history. Without this, a previously-selected layer
    lingers in layer_selected_indices after the active layer changes, so
    SuperSkinListMixin.draw_item() renders it with selected=True,
    active=False (the alert/highlight style) even though only the new
    active layer should appear selected.

    Call this right after the active layer changes as a result of a CRUD
    operation, passing the resulting active slot index.
    """
    storage = obj.superskin_storage
    storage.layer_selected_indices = f",{slot_index},"
    storage.layer_selection_history = str(slot_index)


def _enforce_visualizer_from_tab_state(context):
    """Re-apply the correct viewport shader after a layer mutation.

    Reads ``superskin_is_mask_mode`` directly:
      Mask row active → force MASK visualizer
      otherwise        → restore the mode that was active before MASK (SINGLE/MULTI)
    """
    try:
        ctrl = CoreFacade(context).get_ctrl()
        is_mask = getattr(context.scene, "superskin_is_mask_mode", False)
        if is_mask:
            ctrl.set_visualizer_mode('MASK')
        else:
            ctrl.restore_visualizer_from_mask()
    except Exception:
        pass
    # Redraw all VIEW_3D areas so the shader change is visible instantly
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


_last_known_meta = {}

# {mesh_name: frozenset(vg_name, ...)} — see _reflatten_if_vg_names_changed.
_last_known_vg_names = {}


def sync_layers_to_ui_collection(obj):
    meta_raw = obj.data.get("ss_layers_meta")
    if not meta_raw:
        return
    try:
        meta = json.loads(meta_raw)
    except Exception as e:
        print(f"Layer system synchronisation failed: {e}")
        return

    col = obj.superskin_layers_collection
    active_idx = obj.data.get("ss_active_layer", 0)

    col.clear()
    for display_pos, entry in enumerate(meta):
        slot_idx = entry.get("index", display_pos)
        item = col.add()
        item.name = entry.get("name", f"Layer {display_pos}")
        item.index = slot_idx
        item.visible = entry.get("visible", True)
        if slot_idx == active_idx:
            obj.superskin_layers_idx = display_pos

    global _last_known_meta
    _last_known_meta[obj.data.name] = meta_raw


# ═══════════════════════════════════════════════════════════════════════════
#  Depsgraph / Load handlers (relocated from widget_layers.py)
# ═══════════════════════════════════════════════════════════════════════════

def _reflatten_if_vg_names_changed(obj):
    """Re-flatten storage onto the mesh's real Vertex Groups when the live
    VG name set has changed since the last tick (e.g. a bone's VG was
    renamed away and back).

    flatten_visible_layers_to_mesh() (core/layer_storage/flatten.py) only
    writes a bone's weight into a live VG whose name matches — a name that
    is temporarily absent has its weight silently dropped from every
    flatten that runs while it's gone. Renaming the VG back makes that name
    live again, but nothing about the rename itself re-runs flatten: this
    handler previously only resynced the two UI mirror collections
    (sync_layers_to_ui_collection / sync_bones_to_ui_collection), so the
    real VG stayed stuck at the zero weight last written while the name was
    missing, even though the weight data itself was never lost from
    storage. This is the only call site reacting to VG identity changes, so
    it must also trigger the reflatten, not just the UI resync.

    Skipped in Edit Mode: the active layer's write target there is the
    __ssp_* temp VG bridge, not ss_layer_N, so flatten_visible_layers_to_mesh()
    would read the wrong store (see docs/bug-history/0019).
    """
    if obj.mode == 'EDIT':
        DebugLogService.log(
            "bone_id",
            f"_reflatten_if_vg_names_changed(): obj={obj.name!r} SKIPPED "
            f"(EDIT mode — active layer lives in __ssp_* temp VGs)",
        )
        return

    key = obj.data.name
    # __ssp_* temp VGs come and go on every Edit Mode enter/exit (see
    # temp_vg_bridge.py) -- excluded here so that transition alone doesn't
    # look like a "VG name changed" event and trigger a pointless reflatten
    # (harmless since flatten always recomputes from storage, but noisy and
    # was observed firing mid mode-transition in a captured debug log).
    current_names = frozenset(
        vg.name for vg in obj.vertex_groups if not vg.name.startswith("__ssp_")
    )
    previous_names = _last_known_vg_names.get(key)
    _last_known_vg_names[key] = current_names

    if previous_names is None:
        DebugLogService.log(
            "bone_id",
            f"_reflatten_if_vg_names_changed(): obj={obj.name!r} baseline "
            f"vg_names={sorted(current_names)!r} (first tick for this mesh, "
            f"no reflatten)",
        )
        return

    if previous_names == current_names:
        return  # no change — silent, this branch fires on every tick

    added = current_names - previous_names
    removed = previous_names - current_names
    DebugLogService.log(
        "bone_id",
        f"_reflatten_if_vg_names_changed(): obj={obj.name!r} VG NAME CHANGE "
        f"detected -- added={sorted(added)!r} removed={sorted(removed)!r} -- "
        f"triggering flatten_visible_layers_to_mesh()",
    )

    from ...core.shaders.shader_manager import ShaderManager
    from ...core_subsystems.layer_compositor import LayerCompositor as _LC

    storage = LayerStorageService(obj.data)

    # Diagnostic: does *storage* (the persistent source of truth) actually
    # still hold weight data for the name(s) that just came back? If this
    # is empty, the weight was already gone from storage before this tick
    # ever ran (a pruning bug elsewhere, not a flatten/write problem). If
    # it's non-empty but the real VG still reads 0 after flatten below, the
    # bug is in the flatten/write path itself, not storage.
    if DebugLogService.is_enabled("bone_id"):
        for name in sorted(added):
            per_layer_counts = {}
            for layer_idx, raw in storage.harvest_layer_data_map().items():
                decoded = _LC.decode(raw)
                total = 0.0
                count = 0
                for v_weights in decoded.values():
                    w = v_weights.get(name)
                    if w:
                        total += w
                        count += 1
                if count:
                    per_layer_counts[layer_idx] = (count, round(total, 4))
            DebugLogService.log(
                "bone_id",
                f"_reflatten_if_vg_names_changed(): storage pre-flatten check "
                f"name={name!r} -- per_layer(vert_count, total_weight)="
                f"{per_layer_counts!r}",
            )

    storage.flatten_visible_layers_to_mesh(obj)
    obj.data.update()
    obj.update_tag()

    # Diagnostic: did the actual mesh Vertex Group receive the weight this
    # flatten call just composited? Checks EVERY vertex storage holds weight
    # for (not just one sample) -- a single-vertex sample can land on a
    # vertex a higher, visible layer's mask legitimately overrides to 0 at
    # that specific vertex (normal blending), which looks identical to a
    # real bug if only one vertex is checked. Counting nonzero-vs-zero
    # across the whole set distinguishes "isolated masking, most of the
    # region is fine" from "the whole region actually reads 0 on the mesh".
    if DebugLogService.is_enabled("bone_id"):
        for name in sorted(added):
            vg = obj.vertex_groups.get(name)
            if vg is None:
                DebugLogService.log(
                    "bone_id",
                    f"_reflatten_if_vg_names_changed(): post-flatten check "
                    f"name={name!r} -- NO LIVE VG FOUND (unexpected, name was "
                    f"just added)",
                )
                continue
            storage_v_idxs = set()
            for layer_idx, raw in storage.harvest_layer_data_map().items():
                decoded = _LC.decode(raw)
                for v_idx, v_weights in decoded.items():
                    if v_weights.get(name):
                        storage_v_idxs.add(int(v_idx))
            if not storage_v_idxs:
                DebugLogService.log(
                    "bone_id",
                    f"_reflatten_if_vg_names_changed(): post-flatten check "
                    f"name={name!r} -- no weighted vertex found in storage "
                    f"to check",
                )
                continue
            nonzero = 0
            zero_sample = []
            for v_idx in storage_v_idxs:
                try:
                    real_w = next(
                        (g.weight for g in obj.data.vertices[v_idx].groups
                         if g.group == vg.index),
                        0.0,
                    )
                except Exception as e:
                    real_w = 0.0
                if real_w > 0.001:
                    nonzero += 1
                elif len(zero_sample) < 5:
                    zero_sample.append(v_idx)
            DebugLogService.log(
                "bone_id",
                f"_reflatten_if_vg_names_changed(): post-flatten check "
                f"name={name!r} vg_index={vg.index} -- {nonzero}/{len(storage_v_idxs)} "
                f"storage-weighted vertices came back nonzero on the real VG "
                f"(zero_sample verts={zero_sample!r})",
            )
    ShaderManager.bump_deform_generation()
    obj["__ssp_deform_gen"] = obj.get("__ssp_deform_gen", 0) + 1
    ShaderManager().invalidate_and_redraw()

    # Blender's native Vertex Group Weight Overlay (which is what actually
    # renders the color the user sees when selecting a bone row -- see
    # core/shaders/shader_manager.py's module docstring, single-bone
    # visualization has no custom SuperSkinPro shader anymore) is keyed off
    # obj.vertex_groups.active_index, not off any of the data we just wrote.
    # That index is normally kept in sync by apply_active_bone(), but that
    # function only runs from row-click / mode-transition operators, never
    # from this depsgraph handler. If the bone that just came back from
    # orphan status is the one the user still has selected (tracked by
    # NAME in superskin_storage.active_orphan_name, since orphan rows have
    # no real index to point at), that pointer is still sitting on
    # whatever real VG was active before the bone went orphan -- so the
    # overlay keeps showing a stale/unrelated bone's weight even though
    # the just-restored bone's own data is now correct. Re-point it here so
    # the overlay reflects the restored bone without requiring the user to
    # click the row again.
    storage_prop = obj.superskin_storage
    if storage_prop.active_orphan_name in added:
        restored_name = storage_prop.active_orphan_name
        vg = obj.vertex_groups.get(restored_name)
        if vg is not None:
            storage_prop.active_orphan_name = ""
            storage_prop.last_clicked_index = vg.index
            is_active_obj = obj == getattr(
                bpy.context.view_layer.objects, "active", None
            )
            if is_active_obj:
                try:
                    obj.vertex_groups.active_index = vg.index
                except Exception:
                    pass
            DebugLogService.log(
                "bone_id",
                f"_reflatten_if_vg_names_changed(): obj={obj.name!r} "
                f"active_orphan_name={restored_name!r} was still tracking the "
                f"restored bone -- cleared orphan pointer, re-pointed "
                f"last_clicked_index/vertex_groups.active_index to {vg.index} "
                f"(is_active_obj={is_active_obj})",
            )

    DebugLogService.log(
        "bone_id",
        f"_reflatten_if_vg_names_changed(): obj={obj.name!r} flatten complete, "
        f"deform_gen={ShaderManager.get_deform_generation()}",
    )


@bpy.app.handlers.persistent
def _superskin_layers_depsgraph_handler(scene, depsgraph):
    armature_touched = False
    for update in depsgraph.updates:
        obj = update.id
        if isinstance(obj, bpy.types.Object) and obj.type == 'MESH':
            try:
                if "ss_layers_meta" in obj.data:
                    sync_layers_to_ui_collection(obj)
                    # Same gate as the layers sync above — the Deform
                    # Bones mirror collection only matters for meshes
                    # SuperSkinPro has already initialized a layer system
                    # on, and reuses this handler rather than adding a
                    # second depsgraph subscriber for the same update loop.
                    sync_bones_to_ui_collection(obj)
                    _reflatten_if_vg_names_changed(obj)
            except Exception as e:
                DebugLogService.log(
                    "bone_id",
                    f"_superskin_layers_depsgraph_handler() EXCEPTION on "
                    f"obj={getattr(obj, 'name', '?')!r}: {e!r}\n{traceback.format_exc()}",
                )
        elif (isinstance(obj, bpy.types.Object) and obj.type == 'ARMATURE') \
                or isinstance(obj, bpy.types.Armature):
            # Bone-only edits (e.g. toggling `use_deform`, renaming a bone,
            # applying an armature preset) tag the Armature ID, never the
            # Mesh object — the MESH branch above never sees them, so the
            # bones mirror collection silently goes stale until some
            # unrelated mesh-side update happens to fire. Re-sync every
            # mesh that rigs against this armature so `use_deform` toggles
            # are reflected immediately.
            armature_touched = True

    if armature_touched:
        for mesh_obj in bpy.data.objects:
            if mesh_obj.type != 'MESH' or "ss_layers_meta" not in mesh_obj.data:
                continue
            if not any(m.type == 'ARMATURE' and m.object for m in mesh_obj.modifiers):
                continue
            try:
                sync_bones_to_ui_collection(mesh_obj)
            except Exception as e:
                DebugLogService.log(
                    "bone_id",
                    f"_superskin_layers_depsgraph_handler() ARMATURE-resync EXCEPTION on "
                    f"obj={mesh_obj.name!r}: {e!r}\n{traceback.format_exc()}",
                )


@bpy.app.handlers.persistent
def _superskin_layers_load_handler(dummy):
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and "ss_layers_meta" in obj.data:
            try:
                sync_layers_to_ui_collection(obj)
                sync_bones_to_ui_collection(obj)
            except Exception as e:
                DebugLogService.log(
                    "bone_id",
                    f"_superskin_layers_load_handler() EXCEPTION on "
                    f"obj={obj.name!r}: {e!r}\n{traceback.format_exc()}",
                )

# ═══════════════════════════════════════════════════════════════════════════
#  Registration
# ═══════════════════════════════════════════════════════════════════════════


def register():
    bpy.app.handlers.depsgraph_update_post.append(_superskin_layers_depsgraph_handler)
    bpy.app.handlers.load_post.append(_superskin_layers_load_handler)
    # Unconditional (not DebugLogService-gated) so registration success is
    # verifiable in the system console even before any debug category is
    # switched on in Preferences -- this call was previously unreachable
    # (see interface/utils/__init__.py's register()), so confirming it
    # actually runs is the first thing to check if bone/layer list sync
    # goes stale again.
    print("[SSP] interface.utils.utils: depsgraph_update_post / load_post handlers registered")


def unregister():
    try:
        bpy.app.handlers.load_post.remove(_superskin_layers_load_handler)
    except Exception:
        pass
    try:
        bpy.app.handlers.depsgraph_update_post.remove(_superskin_layers_depsgraph_handler)
    except Exception:
        pass