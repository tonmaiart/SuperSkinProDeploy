"""Scene/edit-mode switching operators for SuperSkinPro.

Split out of ops_interface.py (2026-06) — everything here is about
entering/exiting/toggling Blender's interaction mode (Edit Mode, Pose Mode)
for the active mesh/armature, plus the viewport-gray-out theme dimming that
goes with it. Originally merged from interface/op_scene_modes.py.

Relocated from operators/ops_scene_modes.py to features/controller/ (2026-06).
"""

import bpy
import bmesh

from ...core.facade import CoreFacade
from ...core.bone_identity import BoneIdentityService
from ...interface.utils.utils import _is_valid_mesh, _has_layer_system

# Hoisted from function bodies — converted from absolute 'SuperSkinPro.*'
# imports to relative imports for Blender Extensions Platform compatibility.
from ...core.layer_storage.temp_vg_bridge import (
    has_temp_vgs, delete_temp_vgs, load_layer_to_temp_vgs,
    read_temp_vgs_from_bm,
)
from ...core.layer_storage.storage_service import LayerStorageService
from ...core.layer_storage.geometry import get_unified_mapping
from ...core.facade.write import purge_zeroed_orphans_after_bake
from ...interface.utils import utils as _utils
from ..bone_picker import deform_overlay


# ==============================================================================
# GLOBALS (from op_scene_modes.py)
# ==============================================================================

# Scene mode globals
_original_edge_wire = None
_original_wire_edit = None
_original_vertex = None

# Armature viewport visibility, hidden during Edit Layer Weight so the rig
# doesn't visually clutter the weight-paint view. A single global snapshot
# (not per-space, not read via context.space_data) restored across every
# VIEW_3D space found at restore time -- deliberately the same pattern as
# _save_and_set_gray()/_restore_original_colors() below (global value, no
# space_data dependency) rather than the space_data-guarded overlay block
# further down in this file, specifically so it can also be restored
# reliably from _do_auto_save()'s bpy.app.timers callback, where
# context.space_data is not always populated (see that function's own
# docstring). Project rule: any viewport state this addon changes on
# entering Edit Layer Weight must be restored on every exit path (Save
# Weights, Force Pose Mode, and the Tab/unguarded-exit auto-save guard) --
# see docs/bug-history/0030 for what happens when a second, uncoordinated
# writer of the same viewport property exists instead.
_original_show_armature_viewport = None


# ==============================================================================
# SCENE MODE HELPERS (from op_scene_modes.py)
# ==============================================================================

def _save_and_set_gray():
    global _original_edge_wire, _original_wire_edit, _original_vertex
    theme = bpy.context.preferences.themes[0].view_3d
    _original_edge_wire = tuple(theme.wire)
    _original_wire_edit = tuple(theme.wire_edit)
    _original_vertex = tuple(theme.vertex)
    theme.wire = (0.3, 0.3, 0.3)
    theme.wire_edit = (0.3, 0.3, 0.3)
    theme.vertex = (0.3, 0.3, 0.3)


def _restore_original_colors():
    global _original_edge_wire, _original_wire_edit, _original_vertex
    if _original_edge_wire is None:
        return
    theme = bpy.context.preferences.themes[0].view_3d
    theme.wire = _original_edge_wire
    theme.wire_edit = _original_wire_edit
    theme.vertex = _original_vertex


def _hide_armature_viewport():
    """Hide Armature objects in every VIEW_3D viewport (the "Armature" row
    in the header's Selectability & Visibility popover). Snapshots the
    first space found so `_restore_armature_viewport()` can put it back
    exactly. Iterates `window_manager.windows` rather than reading
    `context.space_data` -- see the module-level
    `_original_show_armature_viewport` comment for why."""
    global _original_show_armature_viewport
    if _original_show_armature_viewport is not None:
        return  # already hidden by an unfinished prior session -- don't overwrite the real snapshot
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    space = area.spaces.active
                    if _original_show_armature_viewport is None:
                        _original_show_armature_viewport = space.show_object_viewport_armature
                    space.show_object_viewport_armature = False
                except Exception:
                    pass


def _restore_armature_viewport():
    """Restore Armature viewport visibility snapshotted by
    `_hide_armature_viewport()`. No-op if nothing is currently hidden."""
    global _original_show_armature_viewport
    if _original_show_armature_viewport is None:
        return
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    area.spaces.active.show_object_viewport_armature = _original_show_armature_viewport
                except Exception:
                    pass
    _original_show_armature_viewport = None


# ==============================================================================
# ENTER / EXIT / TOGGLE EDIT MODE (from op_scene_modes.py)
# ==============================================================================

def _resolve_edit_target_mesh_readonly(context):
    """Read-only mirror of `_enter_edit_mode()`'s own target_mesh resolution
    (same active-object / scene-cache fallback chain), used only by
    `SUPERSKIN_OT_enter_layer_edit.poll()` so the button can stay enabled
    while an Armature (e.g. in Pose Mode) is the active object instead of
    only a mesh.

    Deliberately duplicated rather than shared with `_enter_edit_mode()`:
    that function also *writes* `mw_last_active_armature` as part of
    resolving the target, and poll() is evaluated on every redraw -- far
    too often to perform scene ID-property writes as a side effect. This
    copy drops those writes; execute() (via `_enter_edit_mode()`) still
    performs them exactly as before."""
    obj = context.active_object

    if obj:
        if obj.type == 'MESH':
            return obj
        if obj.type == 'ARMATURE':
            armature = obj
            meshes = [
                o for o in context.view_layer.objects
                if o.type == 'MESH' and any(mod.type == 'ARMATURE' and mod.object == armature for mod in o.modifiers)
            ]
            if meshes:
                cache_key = f"mw_last_mesh_{armature.name}"
                last_mesh_name = context.scene.get(cache_key, "")
                if last_mesh_name in context.view_layer.objects and context.view_layer.objects[last_mesh_name] in meshes:
                    return context.view_layer.objects[last_mesh_name]
                return meshes[0]

    abs_mesh_name = context.scene.get("mw_absolute_last_mesh", "")
    if abs_mesh_name in context.view_layer.objects:
        return context.view_layer.objects[abs_mesh_name]

    armatures = [o for o in context.view_layer.objects if o.type == 'ARMATURE']
    if armatures:
        last_arm_name = context.scene.get("mw_last_active_armature", "")
        armature = context.view_layer.objects.get(last_arm_name) if last_arm_name in context.view_layer.objects else armatures[0]
        meshes = [
            o for o in context.view_layer.objects
            if o.type == 'MESH' and any(mod.type == 'ARMATURE' and mod.object == armature for mod in o.modifiers)
        ]
        if meshes:
            return meshes[0]

    return None


def _enter_edit_mode(op, context):
    """Shared body for entering Edit Mode.

    Takes the invoking operator instance (`op`) so self.report() calls
    are attributed to whichever operator Blender actually invoked from
    the UI/keymap — calling this via a nested `bpy.ops.*()` from another
    operator's execute() would still log the report to the Info editor,
    but Blender suppresses the on-screen report banner for nested calls
    (see OBJECT_OT_mw_toggle_edit_mode, which calls this function directly
    instead of going through bpy.ops for exactly this reason).
    """
    CoreFacade.debug_log(
        "temp_vg",
        f"_enter_edit_mode() ENTRY: active_object={getattr(context.active_object, 'name', None)!r} "
        f"active_object.type={getattr(context.active_object, 'type', None)!r} "
        f"active_object.mode={getattr(context.active_object, 'mode', None)!r}",
    )

    target_mesh = None
    obj = context.active_object

    if obj:
        if obj.type == 'MESH':
            target_mesh = obj
        elif obj.type == 'ARMATURE':
            armature = obj
            context.scene["mw_last_active_armature"] = armature.name
            meshes = [o for o in context.view_layer.objects if o.type == 'MESH' and any(mod.type == 'ARMATURE' and mod.object == armature for mod in o.modifiers)]
            if meshes:
                cache_key = f"mw_last_mesh_{armature.name}"
                last_mesh_name = context.scene.get(cache_key, "")
                if last_mesh_name in context.view_layer.objects and context.view_layer.objects[last_mesh_name] in meshes:
                    target_mesh = context.view_layer.objects[last_mesh_name]
                else:
                    target_mesh = meshes[0]

    if not target_mesh:
        abs_mesh_name = context.scene.get("mw_absolute_last_mesh", "")
        if abs_mesh_name in context.view_layer.objects:
            target_mesh = context.view_layer.objects[abs_mesh_name]
        else:
            armatures = [o for o in context.view_layer.objects if o.type == 'ARMATURE']
            if armatures:
                last_arm_name = context.scene.get("mw_last_active_armature", "")
                armature = context.view_layer.objects.get(last_arm_name) if last_arm_name in context.view_layer.objects else armatures[0]
                meshes = [o for o in context.view_layer.objects if o.type == 'MESH' and any(mod.type == 'ARMATURE' and mod.object == armature for mod in o.modifiers)]
                if meshes:
                    target_mesh = meshes[0]
                    context.scene["mw_last_active_armature"] = armature.name

    if not target_mesh:
        CoreFacade.debug_log("temp_vg", "_enter_edit_mode() ABORT: no target_mesh resolved")
        op.report({'ERROR'}, "No Mesh or Armature found to enter Edit Mode.")
        return {'CANCELLED'}

    CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() target_mesh={target_mesh.name!r}")

    if not any(mod.type == 'ARMATURE' and mod.object for mod in target_mesh.modifiers):
        CoreFacade.debug_log(
            "temp_vg",
            f"_enter_edit_mode() ABORT: {target_mesh.name!r} has no Armature modifier with "
            f"an assigned object — modifiers={[(m.type, getattr(m, 'object', None)) for m in target_mesh.modifiers]!r}",
        )
        op.report({'WARNING'}, "This mesh has no Armature deformer.")
        return {'CANCELLED'}

    # ── Ensure the target can become the active object ──────────────
    if context.active_object != target_mesh:
        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        # Make sure the target is selectable and visible
        target_mesh.hide_select = False
        target_mesh.hide_viewport = False
        target_mesh.hide_set(False)
        context.view_layer.objects.active = target_mesh
        target_mesh.select_set(True)
        # Verify the assignment took effect
        if context.active_object != target_mesh:
            CoreFacade.debug_log(
                "temp_vg",
                f"_enter_edit_mode() ABORT: could not make {target_mesh.name!r} the active "
                f"object — context.active_object is now {getattr(context.active_object, 'name', None)!r}",
            )
            op.report({'ERROR'}, f"Cannot set '{target_mesh.name}' as active object (it may be on a hidden collection or linked).")
            return {'CANCELLED'}

    obj = target_mesh

    for mod in obj.modifiers:
        if mod.type == 'ARMATURE':
            mod.show_in_editmode = True
            mod.show_on_cage = True

    # ── Auto-heal layer storage for topology changes ────────────
    try:
        CoreFacade(context).heal_topology_if_needed()
    except Exception as exc:
        CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() heal_topology_if_needed() raised: {exc!r}")
        pass  # non‑critical — the user can still paint

    # Orphan-bone *visibility* in the Deform Bones list no longer depends
    # on this call — orphan rows are read from obj.superskin_bones_collection
    # (a mirror CollectionProperty kept current by
    # ui.utils.sync_bones_to_ui_collection via the depsgraph_update_post /
    # load_post handlers, through BoneIdentityService.get_scan_for_object's
    # signature-gated read-only scan_readonly()) that recomputes itself
    # automatically whenever vertex-group names, layer data, or the
    # armature's bone set actually changed. This call's only remaining job
    # is refreshing the bone-UUID map (a mesh-data write, which Blender
    # forbids from inside draw() — see bone_identity_service.py's
    # docstring) so the orphan resolver can keep suggesting "RENAMED"
    # targets; it's not required for orphan rows to appear at all.
    try:
        BoneIdentityService(context).backfill_and_scan()
    except Exception as exc:
        CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() backfill_and_scan() raised: {exc!r}")
        pass  # graceful — the UUID-backed rename suggestion is optional

    if obj.mode != 'EDIT':
        if context.active_object is None:
            CoreFacade.debug_log("temp_vg", "_enter_edit_mode() ABORT: active_object is None before first mode_set(EDIT)")
            op.report({'ERROR'}, "No active object – cannot enter Edit Mode.")
            return {'CANCELLED'}
        CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() calling mode_set(EDIT) from mode={obj.mode!r}")
        bpy.ops.object.mode_set(mode='EDIT')
        CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() after first mode_set(EDIT): obj.mode={obj.mode!r}")

    # Load active layer into temp VGs for native BMesh undo tracking.
    # Guard the OBJECT bounce so the auto-save handler does not schedule a
    # spurious bake timer while temp VGs are being initialised.
    _was_suppressing = context.scene.superskin_internal_transaction
    context.scene.superskin_internal_transaction = True
    try:
        CoreFacade.debug_log("temp_vg", "_enter_edit_mode() temp-VG load bounce: mode_set(OBJECT)")
        bpy.ops.object.mode_set(mode='OBJECT')
        _load_active_layer_to_temp_vgs(obj, context)
        CoreFacade.debug_log("temp_vg", "_enter_edit_mode() temp-VG load bounce: mode_set(EDIT)")
        bpy.ops.object.mode_set(mode='EDIT')
    except Exception as exc:
        CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() temp-VG load bounce RAISED: {exc!r}")
        raise
    finally:
        context.scene.superskin_internal_transaction = _was_suppressing
        CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() after temp-VG load bounce: obj.mode={obj.mode!r}")

    bpy.ops.mesh.select_mode(type='VERT')

    saved = context.scene.get(f"mw_saved_selection_{obj.name}", [])
    if saved:
        if context.active_object is None:
            CoreFacade.debug_log("temp_vg", "_enter_edit_mode() ABORT: active_object is None while restoring saved selection")
            op.report({'ERROR'}, "Lost active object while restoring selection.")
            return {'CANCELLED'}
        # Same guard as the temp-VG load bounce above, and for the same
        # reason: this OBJECT->EDIT round trip is purely internal
        # bookkeeping, not a real "user left Edit Mode" event. Without
        # suppressing it, _superskin_auto_save_guard's depsgraph handler
        # sees the mode_set('OBJECT') below as an unguarded EDIT_MESH exit
        # (has_temp_vgs(obj) is already True by this point) and schedules
        # its own async auto-save round trip, which lands back in Object
        # Mode shortly after this operator returns — see the mode-bounce
        # regression this was diagnosed from (obj.mode read 'EDIT' at this
        # function's own completion log but 'OBJECT' moments later at the
        # deferred active-bone sync).
        _was_suppressing_sel = context.scene.superskin_internal_transaction
        context.scene.superskin_internal_transaction = True
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
            for v in obj.data.vertices:
                v.select = v.index in saved
            bpy.ops.object.mode_set(mode='EDIT')
        finally:
            context.scene.superskin_internal_transaction = _was_suppressing_sel

    # context.space_data can be None (F3 search from a non-3D-viewport area,
    # automation/headless invocation) — see the matching guard and note in
    # _exit_edit_mode. Skip the cosmetic overlay setup rather than crashing
    # the whole Enter Edit Mode sequence after the real work above already
    # succeeded.
    # overlay.show_weight ("Vertex Group Weights" checkbox) is deliberately
    # NOT touched here -- features/overlay_color/native_sync.py owns that
    # property's entire lifecycle exclusively via its own poll-based watcher
    # on superskin_active_interface (set below). A direct write here used to
    # race with native_sync.py's snapshot/restore: this function set
    # show_weight=True *before* flipping superskin_active_interface to
    # 'SKINNING', so native_sync.py's _start() (which only fires after
    # observing that flag) snapshotted the already-True value as if it were
    # the user's own prior state, then "restored" show_weight back to True
    # on exit right after _exit_edit_mode()'s own correct False write --
    # see docs/bug-history for the write-up (checkbox left stuck on after
    # Save Weights). See also overlay_color/README.md's "Lifecycle is
    # poll-based, not push-based" invariant.
    space_data = context.space_data
    if space_data is not None and hasattr(space_data, "overlay"):
        overlay = space_data.overlay
        overlay.show_edge_bevel_weight = False
        overlay.show_edge_crease = False
        overlay.show_edge_seams = False
        overlay.show_edge_sharp = False
        overlay.show_faces = False

    _save_and_set_gray()
    _hide_armature_viewport()

    # Which visualizer to show right now is read directly off
    # storage.active_is_mask (the actual source of truth — see
    # apply_active_bone()'s own docstring) rather than through
    # scene.superskin_is_mask_mode, since the full sync below is deferred.
    is_mask = bool(obj.superskin_storage.active_is_mask)

    # Defer the native active-VG pointer sync (storage.last_clicked_index /
    # active_is_mask -> obj.vertex_groups.active_index) instead of calling
    # CoreFacade.apply_active_bone() here inline. That write is a genuine
    # Object-ID mutation, so it lands in the next depsgraph_update_post pass
    # and triggers _superskin_layers_depsgraph_handler's full
    # sync_bones_to_ui_collection() rebuild of the whole Deform Bones mirror
    # collection — on a rig with many bones, doing that synchronously here
    # (stacked on top of heal_topology_if_needed()/backfill_and_scan() a few
    # lines up, which can trigger the same rebuild) was enough added latency
    # that "Edit Layer Weight" looked like it had stopped responding, unlike
    # Tab (which enters native Edit Mode with none of this addon-side setup
    # at all — see docs/bug-history for why that isn't actually the same
    # code path and can't regress this way). A row click already does this
    # exact push via BoneListAdapter.on_single_select() with the same
    # deferral not needed there since it isn't stacked on everything else
    # Entering Edit Mode already does.
    def _sync_active_bone_deferred():
        _ctx = bpy.context
        if _ctx and _ctx.active_object == obj and obj.mode == 'EDIT':
            try:
                CoreFacade(_ctx).apply_active_bone()
                CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() deferred apply_active_bone() done for {obj.name!r}")
            except Exception as exc:
                CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() deferred apply_active_bone() raised: {exc!r}")
        else:
            CoreFacade.debug_log(
                "temp_vg",
                f"_enter_edit_mode() deferred sync skipped: active_object="
                f"{getattr(_ctx.active_object, 'name', None) if _ctx else None!r} obj={obj.name!r} "
                f"obj.mode={obj.mode!r}",
            )
        return None
    bpy.app.timers.register(_sync_active_bone_deferred, first_interval=0.0)

    # Auto-enable the custom weight visualizer (mask-row-aware)
    viz_mode = 'MASK' if is_mask else 'SINGLE'
    try:
        CoreFacade(context).set_visualizer_mode(viz_mode)
    except Exception:
        pass  # graceful — visualizer is optional

    try:
        deform_overlay.show()
    except Exception:
        pass

    try:
        _utils.force_open_super_skin_tab()
    except Exception:
        pass

    CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() COMPLETE: obj={obj.name!r} obj.mode={obj.mode!r} viz_mode={viz_mode!r}")

    context.window_manager.superskin_active_interface = 'SKINNING'

    return {'FINISHED'}


class OBJECT_OT_mw_enter_edit_mode(bpy.types.Operator):
    bl_idname = "object.mw_enter_edit_mode"
    bl_label = "Enter Edit Mode"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return _enter_edit_mode(self, context)


def _exit_edit_mode(op, context, *, keep_panel_open: bool = False):
    """Shared body for exiting Edit Mode — see `_enter_edit_mode` docstring
    for why callers must invoke this directly instead of nesting through
    `bpy.ops.object.mw_exit_edit_mode()`.

    keep_panel_open: when True, skips force_close_tab() so the sidebar
    remains visible after returning to Object Mode (used by Save Weights
    and the auto-save guard, which both want the panel to stay open).
    """
    obj = context.active_object
    if not _is_valid_mesh(obj):
        return {'CANCELLED'}

    _restore_original_colors()
    _restore_armature_viewport()

    # Guard this whole bake -> mode_set(OBJECT) -> delete_temp_vgs sequence
    # with superskin_internal_transaction, exactly like _do_auto_save() does
    # around its own identical sequence. Without this, the mode_set(OBJECT)
    # call below is indistinguishable to _superskin_auto_save_guard's
    # depsgraph_update_post handler from an unguarded Tab-press exit -- its
    # only check is `not scene.superskin_internal_transaction` (see that
    # handler's docstring). Left unset here, a properly-saved exit could
    # itself schedule a redundant _do_auto_save() bounce (mode_set(OBJECT)
    # -> mode_set(EDIT) -> _bake_temp_vgs_on_exit() again), re-baking from
    # whatever BMesh state exists at that later point and silently
    # overwriting the just-committed, correct storage write with stale data
    # -- observed as a weight-apply result on an unlocked orphan bone
    # reverting after Save Weights, even though the bake at the time of the
    # Save Weights click itself was already verified correct.
    scene = context.scene
    prev_transaction = scene.superskin_internal_transaction
    scene.superskin_internal_transaction = True
    try:
        # Bake temp VGs → ss_layer_N before leaving Edit Mode
        _bake_temp_vgs_on_exit(obj)

        # Clean up the custom visualizer before leaving edit mode.
        try:
            CoreFacade(context).set_visualizer_mode('CLEAR')
        except Exception:
            pass

        # Also hide the deform bone overlay — it's Edit-Mode-only and should
        # never carry over into Object Mode.
        try:
            deform_overlay.hide()
        except Exception:
            pass

        bpy.ops.object.mode_set(mode='OBJECT')

        # Delete temp VGs in Object Mode as required by delete_temp_vgs contract.
        try:
            if has_temp_vgs(obj):
                delete_temp_vgs(obj)
        except Exception:
            pass
    finally:
        scene.superskin_internal_transaction = prev_transaction

    selected_indices = [v.index for v in obj.data.vertices if v.select]
    context.scene[f"mw_saved_selection_{obj.name}"] = selected_indices
    context.scene["mw_absolute_last_mesh"] = obj.name

    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object:
            context.scene[f"mw_last_mesh_{mod.object.name}"] = obj.name
            context.scene["mw_last_active_armature"] = mod.object.name

    # context.space_data can be None here — e.g. invoked via F3 search from
    # a non-3D-viewport area, from a script/automation context, or (as
    # found while consolidating the "Save Weight and Exit" button onto this
    # shared path) headless/background execution. The overlay reset is a
    # viewport cosmetic, not part of the actual save — skip it rather than
    # letting the whole bake+exit sequence raise after the real work above
    # has already completed successfully.
    # overlay.show_weight is deliberately NOT reset here -- see the matching
    # comment in _enter_edit_mode() above; native_sync.py's watcher (polling
    # superskin_active_interface, flipped to 'LAYER' below) owns restoring it.
    space_data = context.space_data
    if space_data is not None and hasattr(space_data, "overlay"):
        overlay = space_data.overlay
        overlay.show_edge_bevel_weight = True
        overlay.show_edge_crease = True
        overlay.show_edge_seams = True
        overlay.show_edge_sharp = True
        overlay.show_faces = True

    if not keep_panel_open:
        _utils.force_close_tab()

    context.window_manager.superskin_active_interface = 'LAYER'

    return {'FINISHED'}


class OBJECT_OT_mw_exit_edit_mode(bpy.types.Operator):
    bl_idname = "object.mw_exit_edit_mode"
    bl_label = "Exit Edit Mode"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return _exit_edit_mode(self, context)


class OBJECT_OT_mw_toggle_edit_mode(bpy.types.Operator):
    bl_idname = "object.mw_toggle_edit_mode"
    bl_label = "Toggle Edit Mode"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if obj and obj.mode == 'EDIT':
            return _exit_edit_mode(self, context)
        else:
            return _enter_edit_mode(self, context)


class OBJECT_OT_mw_force_pose_mode(bpy.types.Operator):
    """Force switch to Pose Mode, or toggle back to Object Mode if already in Pose Mode"""
    bl_idname = "object.mw_force_pose_mode"
    bl_label = "Force Pose Mode"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object

        if obj and obj.type == 'ARMATURE' and obj.mode == 'POSE':
            bpy.ops.object.mode_set(mode='OBJECT')
            return {'FINISHED'}

        # ── Enter Pose Mode ──
        if obj and obj.type == 'ARMATURE':
            context.scene["mw_last_active_armature"] = obj.name

        armature_name = context.scene.get("mw_last_active_armature", "")

        if not armature_name and obj and obj.type == 'MESH':
            for mod in obj.modifiers:
                if mod.type == 'ARMATURE' and mod.object:
                    armature_name = mod.object.name
                    context.scene["mw_last_active_armature"] = armature_name
                    break

        if not armature_name or armature_name not in context.view_layer.objects:
            self.report({'WARNING'}, "No previous Armature found in cache or modifiers.")
            return {'CANCELLED'}

        if obj and obj.type == 'MESH' and obj.mode == 'EDIT':
            # Bake temp VGs back to the active layer before leaving Edit
            # Mode — same shared path "Save Weights" and "Save Weight and
            # Exit" use — so switching straight to Pose Mode never drops
            # in-progress weight edits the way a bare mode_set(OBJECT) would.
            result = _exit_edit_mode(self, context, keep_panel_open=True)
            if result != {'FINISHED'}:
                return result
            try:
                CoreFacade(context).finish(color_only=False)
            except Exception as exc:
                print(f"[SuperSkinPro] Save & Enter Pose Mode: finish() failed: {exc}")

        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        armature = context.view_layer.objects[armature_name]
        bpy.ops.object.select_all(action='DESELECT')
        context.view_layer.objects.active = armature
        armature.select_set(True)

        bpy.ops.object.mode_set(mode='POSE')

        # Deliberately does NOT touch the sidebar panel state here (no
        # force_close_tab() / force_open_super_skin_tab() call). Entering
        # Pose Mode used to force-close the tab unconditionally, which
        # fought with "Popup Main Panel"'s toggle and with the panel simply
        # already being open for a reason -- observed as the sidebar
        # visibly collapsing and reopening in quick succession. Pose Mode
        # entry/exit should have zero opinion on panel visibility.
        return {'FINISHED'}


class OBJECT_OT_mw_popup_main_panel(bpy.types.Operator):
    """Toggle the SuperSkinPro sidebar panel without switching scene mode
    or entering Edit Layer Weight — used by the pie menu's non-Edit-Mode
    slot, which previously routed through mw_toggle_edit_mode and forced
    the user straight into layer weight editing.

    A real open/close toggle: pressed again while the panel is already
    expanded, it collapses the sidebar instead of being a no-op re-open."""
    bl_idname = "object.mw_popup_main_panel"
    bl_label = "Popup Main Panel"
    bl_options = {'REGISTER'}

    def execute(self, context):
        if _utils.is_super_skin_tab_open():
            _utils.force_close_tab()
        else:
            _utils.force_open_super_skin_tab()
        return {'FINISHED'}


# ==============================================================================
# TEMP VG HELPERS
# ==============================================================================

# {obj_name: int} -- bumped every time a *fresh* set of temp VGs is loaded
# for that object (a genuine new Edit Mode session, not the internal
# OBJECT<->EDIT bounce used to build them). `_schedule_auto_save()` snapshots
# the token for its object at schedule time; `_do_auto_save()`'s deferred
# timer re-checks it before touching anything. If the token has changed by
# the time the timer fires, a brand-new Edit Mode session (with its own
# freshly loaded, unedited temp VGs) has already started since the stale
# auto-save was scheduled, and the timer must bail instead of baking that
# fresh session's data back into storage and deleting its temp VGs out from
# under the user. See the race described in _exit_edit_mode()'s docstring
# comment: superskin_internal_transaction alone only protects against the
# guard firing *during* the guarded block; it cannot protect against
# depsgraph_update_post being delivered *after* the block's `finally` has
# already reset the flag, which is exactly the window a fast
# Save-Weights-then-Edit-Layer-Weight click sequence can land in.
_temp_vg_session_token: dict = {}
_next_temp_vg_session_token = 0


def _bump_temp_vg_session_token(obj_name: str) -> int:
    global _next_temp_vg_session_token
    _next_temp_vg_session_token += 1
    _temp_vg_session_token[obj_name] = _next_temp_vg_session_token
    return _next_temp_vg_session_token


def _load_active_layer_to_temp_vgs(obj, context):
    """Load the active layer into temp VGs after entering Edit Mode.

    Uses get_unified_mapping() (real VGs + synthetic IDs for orphan bones),
    not get_local_mapping() (real VGs only). load_layer_to_temp_vgs() silently
    drops any layer_dict entry whose bone name isn't a key in id_to_bone, so
    a bone that's orphaned at the exact moment Edit Mode is entered used to
    get no __ssp_N slot at all -- its weight was invisible for the whole
    session, and _bake_temp_vgs_on_exit()'s write_layer_dict() (a full
    REPLACE of the active layer) then permanently erased it from storage on
    exit, even if the user renamed the VG back to restore it mid-session.
    get_unified_mapping() gives orphan bones a synthetic-ID temp VG slot too,
    so their weight round-trips through the Edit Mode session correctly
    regardless of whether the real VG comes back before exit.
    """
    try:
        if has_temp_vgs(obj):
            delete_temp_vgs(obj)

        storage = LayerStorageService(obj.data)
        if not storage.has_layer_system():
            return

        active_idx = storage.get_active_layer_index()
        layer_dict = storage.read_layer_dict(active_idx)
        mask_dict = storage.read_mask_dict(active_idx)
        _, id_to_bone = get_unified_mapping(obj)

        load_layer_to_temp_vgs(obj, layer_dict, mask_dict, active_idx, id_to_bone)
        _bump_temp_vg_session_token(obj.name)
    except Exception as e:
        print(f"[SuperSkinPro] Warning: could not load temp VGs: {e}")


def _bake_temp_vgs_on_exit(obj):
    """Bake temp VGs back to ss_layer_N when exiting Edit Mode.
    Caller must delete temp VGs in OBJECT mode after mode_set."""
    try:

        if not has_temp_vgs(obj):
            return

        from ...core_subsystems.debug_logging import DebugLogService

        orphan_names_before = set()
        if DebugLogService.is_enabled("bone_id"):
            orphan_names_before = {
                item.name for item in obj.superskin_bones_collection if item.is_orphan
            }

        bm = bmesh.from_edit_mesh(obj.data)
        layer_dict, mask_dict, active_idx = read_temp_vgs_from_bm(bm, obj)

        if DebugLogService.is_enabled("bone_id") and orphan_names_before:
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
                f"_bake_temp_vgs_on_exit(): obj={obj.name!r} active_idx={active_idx} "
                f"total_mesh_vertices={total_mesh_verts} "
                f"orphans_before_bake={sorted(orphan_names_before)!r} "
                f"still_present_in_baked_active_layer={sorted(still_in_active_layer)!r} "
                f"(vert_count, first_10_v_idx)={sample!r} "
                f"(empty means the active layer's own bake correctly dropped them; "
                f"non-empty means they still carry weight in the layer being saved -- "
                f"compare vert_count here against the flood's dirty_verts count to see "
                f"if the flood simply missed these specific vertices)",
            )

        storage = LayerStorageService(obj.data)
        old_layer_dict = storage.read_layer_dict(active_idx)
        storage.write_layer_dict(active_idx, layer_dict)
        if mask_dict:
            storage.write_mask_dict(active_idx, mask_dict)
        else:
            storage.delete_mask_property(active_idx)
        purge_zeroed_orphans_after_bake(storage, obj, old_layer_dict, layer_dict)

        if DebugLogService.is_enabled("bone_id") and orphan_names_before:
            from ...core.bone_identity import BoneIdentityService
            after = BoneIdentityService.get_scan_for_object(obj)
            after_map = {e["name"]: e["layer_indices"] for e in after}
            DebugLogService.log(
                "bone_id",
                f"_bake_temp_vgs_on_exit(): obj={obj.name!r} POST-SAVE orphan scan -- "
                f"{orphan_names_before!r} -> still orphaned with layer_indices="
                f"{ {n: after_map.get(n) for n in orphan_names_before} !r} "
                f"(a name missing from this dict entirely means it fully cleared)",
            )
    except Exception as e:
        print(f"[SuperSkinPro] Warning: could not bake temp VGs on exit: {e}")


# ==============================================================================
# MODE-GATE OPERATORS  (UI buttons "Edit Layer Weight" / "Save Weights")
# ==============================================================================

class SUPERSKIN_OT_enter_layer_edit(bpy.types.Operator):
    """Enter Edit Mode and prepare the active layer for weight painting."""
    bl_idname = "superskin.enter_layer_edit"
    bl_label = "Edit"
    bl_description = "Enter Edit Mode to paint weights on the active layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Gated on the decoupled interface state, not context.mode -- a
        # raw Tab press into native Edit Mode (with no prior "Edit Layer
        # Weight" trigger) leaves superskin_active_interface at 'LAYER'
        # (see ops_scene_modes.py's flip points and interface/README.md),
        # and _enter_edit_mode() already handles being invoked from either
        # Object Mode or Edit Mode. Requiring context.mode == 'OBJECT' here
        # would leave this button disabled in exactly that valid case.
        #
        # Also requires the layer system to already be initialised --
        # initialisation is now its own explicit "Initialize Layer" button
        # (features/layer_viewer/ops.py: superskin.layer_init) instead of
        # happening implicitly on first entry here, so this button stays
        # locked until the user has deliberately initialised the mesh.
        #
        # Resolved via _resolve_edit_target_mesh_readonly() rather than
        # requiring context.active_object itself to be a mesh -- clicking
        # this while an Armature is active (e.g. mid Pose Mode) should still
        # work, since _enter_edit_mode() already knows how to resolve and
        # switch to that armature's rigged mesh on its own.
        target_mesh = _resolve_edit_target_mesh_readonly(context)
        return bool(
            target_mesh is not None
            and context.window_manager.superskin_active_interface == 'LAYER'
            and _has_layer_system(target_mesh)
        )

    def execute(self, context):
        return _enter_edit_mode(self, context)


class SUPERSKIN_OT_save_weights(bpy.types.Operator):
    """Bake weight changes back to the active layer and return to Object Mode."""
    bl_idname = "superskin.save_weights"
    bl_label = "Save Weights"
    bl_description = "Save weight edits to the active layer and exit to Object Mode"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH' and context.mode == 'EDIT_MESH')

    def execute(self, context):
        result = _exit_edit_mode(self, context, keep_panel_open=True)
        if result == {'FINISHED'}:
            try:
                CoreFacade(context).finish(color_only=False)
            except Exception as exc:
                print(f"[SuperSkinPro] Save Weights: finish() failed: {exc}")
        return result


# ==============================================================================
# AUTO-SAVE GUARD — bakes temp VGs when user exits Edit Mode via Tab/keymap
# without clicking "Save Weights". Uses depsgraph_update_post to detect the
# unguarded EDIT_MESH → non-EDIT transition.
# ==============================================================================

_mode_guard_last_mode: dict = {}  # {obj_name: mode_string}


@bpy.app.handlers.persistent
def _superskin_auto_save_guard(scene, depsgraph):
    """Detect unguarded Edit-Mode exits and trigger an auto-bake."""
    ctx = bpy.context
    if not ctx:
        return
    obj = ctx.active_object
    if not (obj and obj.type == 'MESH'):
        _mode_guard_last_mode.clear()
        return

    current_mode = ctx.mode
    last_mode = _mode_guard_last_mode.get(obj.name, current_mode)
    _mode_guard_last_mode[obj.name] = current_mode

    if (last_mode == 'EDIT_MESH'
            and current_mode != 'EDIT_MESH'
            and not scene.superskin_internal_transaction):
        ctx.window_manager.superskin_active_interface = 'LAYER'
        try:
            if has_temp_vgs(obj):
                from ...core_subsystems.debug_logging import DebugLogService
                DebugLogService.log(
                    "bone_id",
                    f"_superskin_auto_save_guard(): obj={obj.name!r} detected "
                    f"unguarded EDIT_MESH exit (internal_transaction was False) "
                    f"-- scheduling _do_auto_save(). If this fires right after "
                    f"a proper Save Weights click, it's a redundant re-bake.",
                )
                # Snapshot the current temp-VG session token *now*, while we
                # know these are the temp VGs belonging to the session that
                # is actually exiting. If a new "Edit Layer Weight" click
                # reloads a fresh set of temp VGs before this scheduled
                # timer fires, _bump_temp_vg_session_token() will have moved
                # this object's token forward -- _do_auto_save() re-checks it
                # against this snapshot and bails rather than rebaking and
                # deleting temp VGs the user has already resumed editing.
                _schedule_auto_save(obj.name, _temp_vg_session_token.get(obj.name))
        except Exception:
            pass


def _schedule_auto_save(obj_name: str, expected_session_token) -> None:
    """Register a one-shot timer that bakes temp VGs after an unguarded exit.

    Blender serializes the BMesh (including __ssp_* temp VG weights) back to
    mesh.vertices when Tab is pressed, so re-entering Edit Mode in the timer
    creates a fresh BMesh with the correct weights — making read_temp_vgs_from_bm
    reliable even though the user has already exited.

    Deliberately does NOT call `_exit_edit_mode()` (the shared body used by
    SUPERSKIN_OT_save_weights / OBJECT_OT_mw_exit_edit_mode / the "Save
    Weight and Exit" button) even though the bake step itself
    (`_bake_temp_vgs_on_exit`) is shared. `_exit_edit_mode()` reads
    `context.space_data.overlay` unguarded, which is fine inside a normal
    operator `execute()` (always has full UI context) but is not reliably
    populated inside a `bpy.app.timers` callback — timers run outside the
    UI event loop and `bpy.context` there can have `area`/`region`/
    `space_data` as None. This function intentionally only reuses the parts
    that are safe here (the bake, visualizer clear, deform-overlay hide,
    temp VG deletion) and skips the space_data-dependent overlay-settings
    reset and Tab/selection-cache bookkeeping that a button click can rely on.

    Args:
        expected_session_token: this object's `_temp_vg_session_token` value
            at the moment the unguarded exit was detected. See
            `_superskin_auto_save_guard()`'s call site and the module-level
            `_temp_vg_session_token` docstring for the race this closes.
    """
    def _do_auto_save():
        _ctx = bpy.context
        if not _ctx:
            return None
        _obj = _ctx.view_layer.objects.get(obj_name)
        if not (_obj and _obj.type == 'MESH'):
            return None

        if not has_temp_vgs(_obj):
            from ...core_subsystems.debug_logging import DebugLogService
            DebugLogService.log(
                "bone_id",
                f"_do_auto_save(): obj={obj_name!r} temp VGs already gone "
                f"(deleted by the real save already ran) -- bailing, no re-bake.",
            )
            return None

        current_token = _temp_vg_session_token.get(obj_name)
        if current_token != expected_session_token:
            from ...core_subsystems.debug_logging import DebugLogService
            DebugLogService.log(
                "bone_id",
                f"_do_auto_save(): obj={obj_name!r} STALE -- a new Edit Mode "
                f"session was already loaded (token {expected_session_token!r} "
                f"-> {current_token!r}) since this auto-save was scheduled -- "
                f"bailing WITHOUT rebaking or deleting temp VGs, so the "
                f"already-resumed session's fresh, unedited data is left "
                f"untouched instead of being clobbered by a redundant "
                f"bake-and-delete of a session the user has since moved on from.",
            )
            return None

        from ...core_subsystems.debug_logging import DebugLogService
        DebugLogService.log(
            "bone_id",
            f"_do_auto_save(): obj={obj_name!r} temp VGs STILL PRESENT -- "
            f"proceeding with bounce-and-rebake now.",
        )

        _restore_original_colors()
        _restore_armature_viewport()

        _scene = _ctx.scene
        _prev_flag = _scene.superskin_internal_transaction
        _scene.superskin_internal_transaction = True
        try:
            if _ctx.active_object != _obj:
                _ctx.view_layer.objects.active = _obj
            if _obj.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            # Re-enter Edit Mode so bmesh.from_edit_mesh can read temp VG weights.
            bpy.ops.object.mode_set(mode='EDIT')
            _bake_temp_vgs_on_exit(_obj)
            try:
                CoreFacade(_ctx).set_visualizer_mode('CLEAR')
            except Exception:
                pass
            try:
                deform_overlay.hide()
            except Exception:
                pass
            bpy.ops.object.mode_set(mode='OBJECT')
            if has_temp_vgs(_obj):
                delete_temp_vgs(_obj)
        except Exception as exc:
            print(f"[SuperSkinPro] Auto-save guard bake failed: {exc}")
        finally:
            _scene.superskin_internal_transaction = _prev_flag
        return None

    bpy.app.timers.register(_do_auto_save, first_interval=0.0)


# ==============================================================================
# REGISTRATION
# ==============================================================================

_classes = (
    OBJECT_OT_mw_enter_edit_mode,
    OBJECT_OT_mw_exit_edit_mode,
    OBJECT_OT_mw_toggle_edit_mode,
    OBJECT_OT_mw_force_pose_mode,
    OBJECT_OT_mw_popup_main_panel,
    SUPERSKIN_OT_enter_layer_edit,
    SUPERSKIN_OT_save_weights,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.app.handlers.depsgraph_update_post.append(_superskin_auto_save_guard)


def unregister():
    try:
        bpy.app.handlers.depsgraph_update_post.remove(_superskin_auto_save_guard)
    except Exception:
        pass
    _mode_guard_last_mode.clear()
    _temp_vg_session_token.clear()
    _restore_original_colors()
    _restore_armature_viewport()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)