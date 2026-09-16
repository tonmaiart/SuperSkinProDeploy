"""Layer-metadata CRUD + per-layer state get/set + mask-gap checking.

Every function takes the UIController instance as its first parameter (``ctrl``).
"""

import bmesh
import bpy
import contextlib
from .undo_manager import skin_transaction
from ...core_subsystems.topology_cache_manager import TopologyCacheManager


@contextlib.contextmanager
def _batched_undo(ctrl):
    """Suppress write_pending_meta_list()'s own early undo_push() for the
    duration of a compound layer-CRUD operation (meta-list write, VG
    creation/deletion, layer switch), so the caller's own single final
    checkpoint (see _push_layer_crud_checkpoint()) is the only step
    recorded for the whole action, instead of an extra early one.

    bpy.context.preferences.edit.use_global_undo was tried first here and
    measured to have NO effect on this scenario (one "Add Layer" click
    still produced the same 4 stacked undo steps with it toggled off) --
    it doesn't gate whatever mechanism the nested bpy.ops.object.mode_set()/
    bpy.ops.ed.undo_push() calls use. temp_vg_bridge.suppress_meta_undo_push()
    is a from-scratch replacement under our own direct control; see its
    docstring. The remaining fragmentation source (mode_set(OBJECT)/
    mode_set(EDIT) each pushing their own step) is addressed separately --
    see _seed_temp_vg_layer()'s empty-dict fast path, which skips the mode
    bounce entirely for create_layer's case.

    Only relevant in Edit Mode -- Object Mode already gets one correct,
    unfragmented checkpoint from the operator's own automatic push."""
    if ctrl.obj.mode != 'EDIT':
        yield
        return
    from ..layer_storage.temp_vg_bridge import suppress_meta_undo_push
    with suppress_meta_undo_push():
        yield


def _push_layer_crud_checkpoint(ctrl, message: str) -> None:
    """Explicit final undo checkpoint for the compound layer-CRUD ops
    (create/remove/duplicate/merge). Call AFTER the _batched_undo(ctrl)
    block that wraps the rest of the operation has exited (global undo
    re-enabled) -- this is the one and only step Blender should record for
    the whole action.

    Only relevant in Edit Mode -- Object Mode already gets a correct
    checkpoint from the operator's own implicit push."""
    if ctrl.obj.mode == 'EDIT':
        try:
            bpy.ops.ed.undo_push(message=message)
        except Exception:
            pass


def create_layer(ctrl, name: str) -> int:
    from ..layer_storage.temp_vg_bridge import effective_meta_list, write_effective_meta_list

    with _batched_undo(ctrl):
        meta, new_idx = ctrl._layer_mgr.create_layer(effective_meta_list(ctrl.obj, ctrl.storage), name)
        write_effective_meta_list(ctrl.obj, ctrl.storage, meta)

        if ctrl.obj.mode == 'EDIT':
            # Give the new (empty) Layer its own resident temp-VG set instead of
            # writing ss_layer_N directly -- permanent storage for a Layer
            # created mid-session is deferred to Exit Edit Mode flush, same as
            # every other structural change (see flush_edit_session()).
            _seed_temp_vg_layer(ctrl, new_idx, {}, {})
        else:
            ctrl.storage.write_layer_dict(new_idx, {})

        switch_to_layer(ctrl, new_idx, push_undo=False)

    # No explicit _push_layer_crud_checkpoint() here, unlike the other three
    # compound ops -- create_layer() is the one case where _seed_temp_vg_layer()
    # takes its empty-dict fast path (see that function's docstring), so
    # there's no mode_set() bounce left in this call chain at all, and
    # write_effective_meta_list() writes via BMesh customdata rather than an
    # ID Property. Both were the preconditions docs/bug-history/0032 found
    # make the wrapping operator's own automatic end-of-execute() undo push
    # unreliable -- neither applies here anymore, so the operator's own
    # "Add Weight Layer" step should already correctly capture the whole
    # action by itself. If real-world testing finds this insufficient,
    # reinstate _push_layer_crud_checkpoint(ctrl, "Add Layer") here.
    return new_idx


def remove_layer(ctrl, index: int):
    """Remove a layer by slot index. No minimum is enforced any more -- the
    very last layer can be removed too, leaving an empty (but still
    present, ``"ss_layers_meta" in mesh`` stays True) Layer list. The
    flatten/composite pipeline already treats an empty layer list as "zero
    weight everywhere", the same result as every layer being hidden, so
    nothing downstream needs a real layer to fall back on."""
    from ..layer_storage.temp_vg_bridge import (
        effective_meta_list, write_effective_meta_list, has_layer_cache,
    )

    with _batched_undo(ctrl):
        meta_list = effective_meta_list(ctrl.obj, ctrl.storage)
        meta = ctrl._layer_mgr.remove_layer(meta_list, index)
        write_effective_meta_list(ctrl.obj, ctrl.storage, meta)

        if ctrl.obj.mode == 'EDIT':
            if has_layer_cache(ctrl.obj, index):
                _delete_temp_vgs_in_object_mode(ctrl, index)
            # ss_layer_N/ss_mask_N are intentionally left untouched here -- a
            # Layer removed mid-session is only permanently deleted at Exit
            # Edit Mode flush (flush_edit_session(), which diffs the
            # entry-state vs. final pending meta list). Deleting it here
            # immediately is exactly the "dangling resurrection" bug this
            # per-Layer redesign structurally eliminates -- the old
            # single-active-layer bake used to resurrect a just-deleted
            # ss_layer_N property on the very next switch (see
            # switch_to_layer()'s own docstring).
        else:
            ctrl.storage.delete_layer_property(index)
            ctrl.storage.delete_mask_property(index)

        if not meta:
            # Nothing left to switch to -- just re-flatten/redraw against the
            # now-empty list and leave the active-layer pointer as-is (it will
            # be overwritten the next time a Layer is created).
            ctrl._finish()
            ctrl.check_for_mask_gaps()
        elif ctrl.active_layer_index == index:
            switch_to_layer(ctrl, meta[0]["index"], push_undo=False)
        else:
            ctrl._finish()
            ctrl.check_for_mask_gaps()

    _push_layer_crud_checkpoint(ctrl, "Remove Layer")


@skin_transaction(color_only=False, check_mask_gaps=True)
def move_layer(ctrl, index: int, direction: int) -> bool:
    from ..layer_storage.temp_vg_bridge import effective_meta_list, write_effective_meta_list

    original = effective_meta_list(ctrl.obj, ctrl.storage)
    meta = ctrl._layer_mgr.move_layer(original, index, direction)
    if meta == original:
        return False
    write_effective_meta_list(ctrl.obj, ctrl.storage, meta)
    return True


def duplicate_layer(ctrl, index: int) -> int:
    from ..layer_storage.temp_vg_bridge import (
        effective_meta_list, write_effective_meta_list, has_layer_cache, read_temp_vgs_from_bm,
    )

    with _batched_undo(ctrl):
        meta, new_idx = ctrl._layer_mgr.duplicate_layer(effective_meta_list(ctrl.obj, ctrl.storage), index)
        if new_idx is None:
            return -1
        write_effective_meta_list(ctrl.obj, ctrl.storage, meta)

        if ctrl.obj.mode == 'EDIT':
            if has_layer_cache(ctrl.obj, index):
                # Source Layer has live, unbaked session edits -- clone THOSE,
                # not stale ss_layer_N (the old single-active-layer design
                # silently duplicated pre-session data here whenever the
                # source was the active layer -- see switch_to_layer()'s
                # docstring for the fuller history of bugs this fixes).
                bm = bmesh.from_edit_mesh(ctrl.mesh)
                layer_dict, mask_dict, _ = read_temp_vgs_from_bm(bm, ctrl.obj, layer_idx=index)
            else:
                layer_dict = ctrl.storage.read_layer_dict(index)
                mask_dict = ctrl.storage.read_mask_dict(index)
            _seed_temp_vg_layer(ctrl, new_idx, layer_dict, mask_dict)
        else:
            ctrl.storage.clone_layer_properties(index, new_idx)

        switch_to_layer(ctrl, new_idx, push_undo=False)

    _push_layer_crud_checkpoint(ctrl, "Duplicate Layer")
    return new_idx


def merge_selected_layers(ctrl, selected_indices: list, target_index: int) -> bool:
    """Bridge: harvest bpy data, call core_subsystems.layer_merge, then write back.

    Returns False when preconditions fail; True on success.
    """
    from ...core_subsystems.layer_compositor import LayerCompositor as _LC
    from ..layer_storage.temp_vg_bridge import (
        effective_meta_list, write_effective_meta_list, has_layer_cache,
        read_temp_vgs_from_bm,
    )

    in_edit = ctrl.obj.mode == 'EDIT'

    meta_list = effective_meta_list(ctrl.obj, ctrl.storage)
    num_verts = len(ctrl.mesh.vertices)
    layer_data_map = ctrl.storage.harvest_layer_data_map()
    mask_data_map = ctrl.storage.harvest_mask_data_map()

    if in_edit:
        # Any selected Layer with its own resident temp-VG set holds live,
        # unbaked edits -- override its entry with an ENCODED snapshot of
        # that live data. core_subsystems/layer_compositor/merge.py calls
        # decode_layer_dict() directly on whatever's in these maps, which
        # (unlike codec._composite_layers()'s own harvest loop) has NO
        # raw-dict fast path -- it returns {} for anything that isn't a
        # string, so a raw dict here would silently merge as empty data.
        bm = None
        for l_idx in selected_indices:
            if not has_layer_cache(ctrl.obj, l_idx):
                continue
            if bm is None:
                bm = bmesh.from_edit_mesh(ctrl.mesh)
            layer_dict, mask_dict, _ = read_temp_vgs_from_bm(bm, ctrl.obj, layer_idx=l_idx)
            layer_data_map[l_idx] = _LC.encode(layer_dict)
            if mask_dict:
                mask_data_map[l_idx] = _LC.encode(mask_dict)
            elif l_idx in mask_data_map:
                del mask_data_map[l_idx]

    result = _LC.merge_selected(
        meta_list, layer_data_map, mask_data_map,
        selected_indices, target_index, num_verts,
    )
    if result is None:
        return False

    merged_weight_dict, merged_mask_dict, new_meta_list = result

    others = sorted((i for i in selected_indices if i != target_index), reverse=True)

    with _batched_undo(ctrl):
        if in_edit:
            _seed_temp_vg_layer(ctrl, target_index, merged_weight_dict, merged_mask_dict)
            for idx in others:
                if has_layer_cache(ctrl.obj, idx):
                    _delete_temp_vgs_in_object_mode(ctrl, idx)
            # ss_layer_N/ss_mask_N for target_index and the merged-away
            # `others` are intentionally left untouched here -- permanent
            # storage only reflects the merge result at Exit Edit Mode flush.
        else:
            ctrl.storage.write_layer_dict(target_index, merged_weight_dict)
            if merged_mask_dict:
                ctrl.storage.write_mask_dict(target_index, merged_mask_dict)
            else:
                # Degenerate case: every selected layer had no mask coverage.
                ctrl.storage.delete_mask_property(target_index)
            # Delete raw storage for the removed layers (highest slot first to
            # avoid index shifts on subsequent deletions within the same loop).
            for idx in others:
                ctrl.storage.delete_layer_property(idx)
                ctrl.storage.delete_mask_property(idx)

        write_effective_meta_list(ctrl.obj, ctrl.storage, new_meta_list)

        if ctrl.active_layer_index != target_index:
            switch_to_layer(ctrl, target_index, push_undo=False)
        else:
            ctrl._finish()
        ctrl.check_for_mask_gaps()

    _push_layer_crud_checkpoint(ctrl, "Merge Layers")
    return True


@skin_transaction(color_only=False, check_mask_gaps=True)
def toggle_visible(ctrl, index: int):
    from ..layer_storage.temp_vg_bridge import effective_meta_list, write_effective_meta_list

    meta = ctrl._layer_mgr.toggle_visible(effective_meta_list(ctrl.obj, ctrl.storage), index)
    write_effective_meta_list(ctrl.obj, ctrl.storage, meta)


def rename_layer(ctrl, index: int, new_name: str):
    from ..layer_storage.temp_vg_bridge import effective_meta_list, write_effective_meta_list

    meta = ctrl._layer_mgr.rename_layer(effective_meta_list(ctrl.obj, ctrl.storage), index, new_name)
    write_effective_meta_list(ctrl.obj, ctrl.storage, meta)


def get_layer_icon(ctrl, index: int = None) -> str:
    from ..layer_storage.temp_vg_bridge import effective_meta_list
    if index is None:
        index = ctrl.active_layer_index
    return ctrl._layer_mgr.get_icon(effective_meta_list(ctrl.obj, ctrl.storage), index)


def set_layer_icon(ctrl, index: int, icon: str):
    from ..layer_storage.temp_vg_bridge import effective_meta_list, write_effective_meta_list

    meta = ctrl._layer_mgr.set_icon(effective_meta_list(ctrl.obj, ctrl.storage), index, icon)
    write_effective_meta_list(ctrl.obj, ctrl.storage, meta)


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
            _switch_temp_vg_layer(ctrl, index)
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


def _switch_temp_vg_layer(ctrl, new_layer_index: int):
    """In Edit Mode: switch which Layer's temp-VG set is active.

    If new_layer_index already has its own resident temp-VG set (visited
    earlier this session), this is pure bookkeeping -- __ssp_meta_layer
    flips to point at it, nothing is baked, no VGs are created or deleted,
    and no Object<->Edit mode bounce is needed at all. The outgoing Layer's
    own VG set is left exactly as it was, ready to switch back to later --
    unlike the old single-active-layer design, nothing here ever bakes a
    Layer's weights back to ss_layer_N/ss_mask_N; that permanent-storage
    write now only happens once, on real Exit Edit Mode, covering every
    Layer touched this session at once.

    Otherwise (first visit this session), one Object<->Edit bounce loads a
    fresh VG set for it from its true last-flushed ss_layer_N/ss_mask_N --
    the Python VG-creation API only works in Object Mode. Nothing about any
    OTHER already-resident Layer's own VG set is touched either way. The
    multi-select pool (__ssp_pool) needs no bake/restore dance around this
    bounce anymore either -- it's a session-wide marker now created once
    (at Enter Edit Mode, before any switch can run) and never recreated by
    a later load_layer_to_temp_vgs() call, so it simply survives this
    bounce untouched, the same way an already-resident Layer's own switch
    leaves it untouched.

    Blender still records one memfile undo step per switch either way (any
    `'UNDO'`-flagged operator call gets one automatically, whether or not
    its body does a mode bounce), and __ssp_meta_layer records the new
    active Layer so undo_post can restore the correct one when Ctrl+Z
    fires (core/ui_controller/undo_manager.py's _sync_after_undo()).

    Note: ss_layers_meta's per-layer selection/lock/active-bone bookkeeping
    (_save_current_layer_state()/_restore_layer_state() below) is left
    unchanged from before this redesign -- both branches still write/read
    it immediately, same as always. That's UI selection state, not weight
    data or structural layer CRUD, so deferring it is out of scope here.
    """
    from ..layer_storage.temp_vg_bridge import has_layer_cache, load_layer_to_temp_vgs

    obj = ctrl.obj

    if has_layer_cache(obj, new_layer_index):
        ctrl._save_current_layer_state()
        obj["__ssp_meta_layer"] = new_layer_index
        ctrl.active_layer_index = new_layer_index
        ctrl._restore_layer_state()
        return

    was_suppressing = ctrl.ctx.scene.superskin_internal_transaction
    ctrl.ctx.scene.superskin_internal_transaction = True

    try:
        bpy.ops.object.mode_set(mode='OBJECT')

        ctrl._save_current_layer_state()
        ctrl.active_layer_index = new_layer_index

        new_layer_dict = ctrl.storage.read_layer_dict(new_layer_index)
        new_mask_dict = ctrl.storage.read_mask_dict(new_layer_index)
        _, id_to_bone = TopologyCacheManager.get_local_mapping(ctrl.obj, ctrl.storage)

        ctrl._restore_layer_state()

        load_layer_to_temp_vgs(obj, new_layer_dict, new_mask_dict, new_layer_index, id_to_bone)

        bpy.ops.object.mode_set(mode='EDIT')

    finally:
        ctrl.ctx.scene.superskin_internal_transaction = was_suppressing
        # Guarantee re-entry into Edit Mode even if an intermediate step raised.
        if ctrl.obj.mode != 'EDIT':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except Exception:
                pass


def _seed_temp_vg_layer(ctrl, layer_index: int, layer_dict: dict, mask_dict: dict):
    """In Edit Mode: create (or replace) one Layer's own resident temp-VG
    set from the given weight/mask dicts, without touching permanent
    storage at all. Scoped to only this one Layer -- every other resident
    Layer's own VG set is untouched.

    Used by create_layer (always empty dicts), duplicate_layer/
    merge_selected_layers (dicts read from a source Layer's own live
    session data when it has one, instead of stale permanent storage).

    Uses TopologyCacheManager.get_local_mapping() (real VGs only), matching
    _switch_temp_vg_layer()'s own first-visit branch -- this mirrors a
    pre-existing limitation of the switch path (not something newly
    introduced here): orphan bones only get a temp-VG slot via
    get_unified_mapping() at the original Enter Edit Mode load
    (features/controller/ops_scene_modes.py's _load_active_layer_to_temp_vgs()),
    not on a later switch or a new Layer created mid-session.

    EMPTY-DICT FAST PATH (no mode bounce): the original implementation
    always did an Object<->Edit mode_set() round trip before calling
    load_layer_to_temp_vgs(), on the assumption that VG creation only
    works in Object Mode. Directly confirmed false on this Blender version
    (obj.vertex_groups.new() succeeds while genuinely in Edit Mode) -- and
    that mode_set() round trip was the direct cause of Add Layer producing
    multiple fragmented, separately-Ctrl+Z-able undo steps instead of one
    (each mode_set() call pushes its own automatic step). When layer_dict
    and mask_dict are both empty (create_layer()'s call pattern, always),
    load_layer_to_temp_vgs() never calls vg.add() at all -- no weight data
    exists to write -- so there is no risk from its OTHER, unverified,
    Object-Mode-style write calls (vg.add() specifically has NOT been
    re-verified safe in Edit Mode for the non-empty case, so
    duplicate_layer/merge_selected_layers -- which pass real weight data --
    still go through the mode-bounce path below unchanged)."""
    from ..layer_storage.temp_vg_bridge import load_layer_to_temp_vgs

    obj = ctrl.obj

    if not layer_dict and not mask_dict:
        _, id_to_bone = TopologyCacheManager.get_local_mapping(ctrl.obj, ctrl.storage)
        load_layer_to_temp_vgs(obj, layer_dict, mask_dict, layer_index, id_to_bone)
        return

    was_suppressing = ctrl.ctx.scene.superskin_internal_transaction
    ctrl.ctx.scene.superskin_internal_transaction = True
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
        _, id_to_bone = TopologyCacheManager.get_local_mapping(ctrl.obj, ctrl.storage)
        load_layer_to_temp_vgs(obj, layer_dict, mask_dict, layer_index, id_to_bone)
        bpy.ops.object.mode_set(mode='EDIT')
    finally:
        ctrl.ctx.scene.superskin_internal_transaction = was_suppressing
        if ctrl.obj.mode != 'EDIT':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except Exception:
                pass


def _delete_temp_vgs_in_object_mode(ctrl, layer_idx: int):
    """Bounce to OBJECT mode just long enough to delete one Layer's own
    resident temp-VG set, then return to EDIT.

    ``delete_temp_vgs()`` (``core/layer_storage/temp_vg_bridge.py``) must be
    called in OBJECT mode -- every other call site in the codebase already
    bounces mode before calling it (e.g.
    ``features/controller/ops_scene_modes.py``'s exit path, and this
    module's own ``load_layer_to_temp_vgs()`` callers via
    ``_seed_temp_vg_layer()``/``_switch_temp_vg_layer()``). Used by
    ``remove_layer()``/``merge_selected_layers()`` when a Layer with its own
    resident temp-VG set is deleted mid-session -- unlike those two
    functions, there is no VG data to (re)create here, only to remove."""
    from ..layer_storage.temp_vg_bridge import delete_temp_vgs

    obj = ctrl.obj
    was_suppressing = ctrl.ctx.scene.superskin_internal_transaction
    ctrl.ctx.scene.superskin_internal_transaction = True
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
        delete_temp_vgs(obj, layer_idx=layer_idx)
        bpy.ops.object.mode_set(mode='EDIT')
    finally:
        ctrl.ctx.scene.superskin_internal_transaction = was_suppressing
        if obj.mode != 'EDIT':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except Exception:
                pass


def flush_edit_session(ctrl):
    """Flush every resident Layer's in-session edits -- weights, mask, and
    structural metadata (Add/Remove/Duplicate/Merge/Move/Rename/Visibility)
    -- to permanent storage. Call exactly once, on a real Exit Edit Mode
    (Save Weights / Force Pose Mode / the auto-save guard's unguarded-exit
    path) -- never on a mid-session Layer switch, which no longer bakes at
    all (see _switch_temp_vg_layer()).

    Must be called while STILL in EDIT mode (reads the live edit-BMesh
    directly, same as the per-weight-op hot path) -- the caller bounces to
    Object Mode and deletes every temp VG (delete_temp_vgs(obj)) AFTER this
    returns, mirroring the previous single-active-layer bake function's own
    contract (features/controller/ops_scene_modes.py's
    _bake_temp_vgs_on_exit(), now a thin wrapper around this).
    """
    from ..layer_storage.temp_vg_bridge import (
        has_temp_vgs, has_layer_cache, read_temp_vgs_from_bm,
        read_pending_meta_list, read_full_pool,
    )
    from ...core_subsystems.debug_logging import DebugLogService

    obj = ctrl.obj
    if not has_temp_vgs(obj):
        return

    bm = bmesh.from_edit_mesh(obj.data)

    # Bake the multi-select pool (__ssp_pool, undo-safe during the session
    # -- see docs/bug-history's follow-up to 0032) back into
    # storage.selected_names before it's read below. Real bones come from
    # the BMesh-tracked pool VG; orphan bones (no real VG) come from the
    # parallel string property, which never had a live temp-VG mirror
    # during the session in the first place. Global/session-wide,
    # independent of which Layer(s) get baked below.
    storage_obj = obj.superskin_storage
    merged_pool = read_full_pool(obj, bm)
    storage_obj.selected_names = f",{','.join(sorted(merged_pool))}," if merged_pool else ","
    storage_obj.selected_orphan_names = ""

    entry_meta = ctrl.storage.read_meta_list()
    entry_indices = {layer.get("index") for layer in entry_meta}

    pending = read_pending_meta_list(obj)
    if pending is None:
        # No structural CRUD ran this session (the pending mirror was never
        # written) -- fall back to the permanent list as-is, same
        # membership as before this Layer's session started.
        pending = entry_meta
    final_indices = {layer.get("index") for layer in pending}

    active_idx = int(obj.get("__ssp_meta_layer", ctrl.active_layer_index))

    orphan_names_before = set()
    if DebugLogService.is_enabled("bone_id"):
        orphan_names_before = {
            item.name for item in obj.superskin_bones_collection if item.is_orphan
        }

    active_old_layer_dict = None
    active_new_layer_dict = None

    for layer in pending:
        l_idx = layer.get("index")
        if l_idx is None or not has_layer_cache(obj, l_idx):
            continue

        layer_dict, mask_dict, _ = read_temp_vgs_from_bm(bm, obj, layer_idx=l_idx)

        if l_idx == active_idx and DebugLogService.is_enabled("bone_id") and orphan_names_before:
            names_in_baked_layer = set()
            per_name_verts = {}
            for v_idx, weights in layer_dict.items():
                for name in orphan_names_before:
                    if name in weights:
                        names_in_baked_layer.add(name)
                        per_name_verts.setdefault(name, []).append(v_idx)
            still_in_active_layer = orphan_names_before & names_in_baked_layer
            total_mesh_verts = len(obj.data.vertices)
            sample = {
                name: (len(verts), verts[:10])
                for name, verts in per_name_verts.items()
            }
            DebugLogService.log(
                "bone_id",
                f"flush_edit_session(): obj={obj.name!r} active_idx={active_idx} "
                f"total_mesh_vertices={total_mesh_verts} "
                f"orphans_before_bake={sorted(orphan_names_before)!r} "
                f"still_present_in_baked_active_layer={sorted(still_in_active_layer)!r} "
                f"(vert_count, first_10_v_idx)={sample!r} "
                f"(empty means the active layer's own bake correctly dropped them; "
                f"non-empty means they still carry weight in the layer being saved -- "
                f"compare vert_count here against the flood's dirty_verts count to see "
                f"if the flood simply missed these specific vertices)",
            )

        old_layer_dict = ctrl.storage.read_layer_dict(l_idx)
        ctrl.storage.write_layer_dict(l_idx, layer_dict)
        if mask_dict:
            ctrl.storage.write_mask_dict(l_idx, mask_dict)
        else:
            ctrl.storage.delete_mask_property(l_idx)

        if l_idx == active_idx:
            active_old_layer_dict = old_layer_dict
            active_new_layer_dict = layer_dict

    # Layers removed/merged away this session (present at entry, gone from
    # the final pending list) must have their permanent storage deleted,
    # not left dangling.
    for l_idx in (entry_indices - final_indices):
        ctrl.storage.delete_layer_property(l_idx)
        ctrl.storage.delete_mask_property(l_idx)

    ctrl.storage.write_meta_list(pending)

    # Orphan-bone cleanup only ever needs to run against what happened to
    # the ACTIVE layer this session (the only one actually paintable) --
    # matching purge_zeroed_orphans_after_bake()'s original contract
    # exactly (previously called once per Exit Edit Mode, for the single
    # active layer only). Deliberately run AFTER write_meta_list() above,
    # not inside the per-layer loop or before it: this function does its
    # OWN internal read-modify-write of ss_layers_meta/ss_layer_N
    # (core/facade/write.py's _purge_zeroed_orphans_from_all_layers()), so
    # it must see the FINAL pending meta list already committed, not a
    # pre-flush snapshot this function's own write above would otherwise
    # silently clobber (and calling it once per resident Layer would also
    # be wrong -- its internal "skip the active layer" logic assumes
    # exactly one call per flush, against the true active layer).
    if active_old_layer_dict is not None:
        ctrl.purge_zeroed_orphans_after_bake(active_old_layer_dict, active_new_layer_dict)

    if DebugLogService.is_enabled("bone_id") and orphan_names_before:
        from ..bone_identity import BoneIdentityService
        after = BoneIdentityService.get_scan_for_object(obj)
        after_map = {e["name"]: e["layer_indices"] for e in after}
        DebugLogService.log(
            "bone_id",
            f"flush_edit_session(): obj={obj.name!r} POST-SAVE orphan scan -- "
            f"{orphan_names_before!r} -> still orphaned with layer_indices="
            f"{ {n: after_map.get(n) for n in orphan_names_before} !r} "
            f"(a name missing from this dict entirely means it fully cleared)",
        )


def layer_meta_list(ctrl) -> list:
    from ..layer_storage.temp_vg_bridge import effective_meta_list
    return effective_meta_list(ctrl.obj, ctrl.storage)


def get_bone_locks(ctrl, layer_index: int = None) -> dict:
    from ..layer_storage.temp_vg_bridge import effective_meta_list
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    return ctrl._layer_mgr.get_bone_locks(effective_meta_list(ctrl.obj, ctrl.storage), layer_index)


def set_bone_locks(ctrl, bone_locks: dict, layer_index: int = None):
    from ..layer_storage.temp_vg_bridge import effective_meta_list, write_effective_meta_list
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    meta = effective_meta_list(ctrl.obj, ctrl.storage)
    meta = ctrl._layer_mgr.set_bone_locks(meta, layer_index, bone_locks)
    write_effective_meta_list(ctrl.obj, ctrl.storage, meta)


def apply_bone_locks(ctrl):
    locks = get_bone_locks(ctrl)
    for item in ctrl.obj.superskin_bones_collection:
        item.lock_weight = locks.get(item.name, False)


def get_selected_bones(ctrl, layer_index: int = None) -> str:
    from ..layer_storage.temp_vg_bridge import effective_meta_list
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    return ctrl._layer_mgr.get_selected_bones(effective_meta_list(ctrl.obj, ctrl.storage), layer_index)


def set_selected_bones(ctrl, selected_names: str, layer_index: int = None):
    from ..layer_storage.temp_vg_bridge import effective_meta_list, write_effective_meta_list
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    meta = effective_meta_list(ctrl.obj, ctrl.storage)
    meta = ctrl._layer_mgr.set_selected_bones(meta, layer_index, selected_names)
    write_effective_meta_list(ctrl.obj, ctrl.storage, meta)


def apply_selected_bones(ctrl):
    sel = get_selected_bones(ctrl)
    if not sel or not sel.startswith(","):
        sel = "," + (sel or "")
    try:
        ctrl.obj.superskin_storage.selected_names = sel
    except Exception:
        pass


def get_active_bone_name(ctrl, layer_index: int = None) -> str:
    from ..layer_storage.temp_vg_bridge import effective_meta_list
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    return ctrl._layer_mgr.get_active_bone_name(effective_meta_list(ctrl.obj, ctrl.storage), layer_index)


def set_active_bone_name(ctrl, name: str, layer_index: int = None):
    from ..layer_storage.temp_vg_bridge import effective_meta_list, write_effective_meta_list
    if layer_index is None:
        layer_index = ctrl.active_layer_index
    meta = effective_meta_list(ctrl.obj, ctrl.storage)
    meta = ctrl._layer_mgr.set_active_bone_name(meta, layer_index, name)
    write_effective_meta_list(ctrl.obj, ctrl.storage, meta)


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
    # Also set Blender's native active_index so the built-in Vertex Group
    # Weight Overlay renders the correct weights without a custom GPU shader.
    try:
        if obj.mode == 'EDIT':
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
    ctrl.storage.write_meta_list([{"name": "Base", "index": 0, "visible": True, "bone_locks": {}, "icon": "NONE"}])
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
    from ..layer_storage.temp_vg_bridge import effective_meta_list, has_layer_cache, read_temp_vgs_from_bm

    meta = effective_meta_list(ctrl.obj, ctrl.storage)
    num_verts = len(ctrl.mesh.vertices)

    in_edit = ctrl.obj.mode == 'EDIT'
    bm = None
    mask_dicts_map = {}
    for layer in meta:
        l_idx = layer["index"]
        if in_edit and has_layer_cache(ctrl.obj, l_idx):
            if bm is None:
                bm = bmesh.from_edit_mesh(ctrl.mesh)
            _, mask_dict, _ = read_temp_vgs_from_bm(bm, ctrl.obj, layer_idx=l_idx)
            mask_dicts_map[l_idx] = mask_dict
        else:
            mask_dicts_map[l_idx] = ctrl.storage.read_mask_dict(l_idx)

    gap_vertices = ctrl._layer_mgr.find_mask_gaps(meta, mask_dicts_map, num_verts)

    if gap_vertices:
        msg = "Mask Gap Detected: please recheck Layer Mask / Skin Weight coverage for gaps."
        print(f"\n❌ [SuperSkinPro ERROR] {msg}\n")
        ctrl.show_report(msg)
        return True
    return False
