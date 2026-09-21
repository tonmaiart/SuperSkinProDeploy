"""Scene/edit-mode switching operators for SuperSkinPro."""

import bpy

from ...core.facade import CoreFacade
from ...core.bone_identity import BoneIdentityService
from ...interface.utils.utils import _is_valid_mesh, _has_layer_system

from ...core.layer_storage.temp_vg_bridge import (
    has_temp_vgs, delete_temp_vgs, layer_mask_default, load_layer_to_temp_vgs,
)
from ...core.layer_storage.storage_service import LayerStorageService
from ...core.layer_storage.geometry import get_unified_mapping
from ...interface.utils import utils as _utils
from ..bone_picker import deform_overlay
from ..weight_apply.public_api import force_hide_brush_hover
from ..deform_layer_viewer.layer_viewer.public_api import get_effective_mesh, get_effective_armature
from . import native_weight_guard


# ==============================================================================
# GLOBALS
# ==============================================================================

_original_edge_wire = None
_original_wire_edit = None
_original_vertex = None

_original_show_bones = None

# Snapshot of the mesh's own vertex-selection paint-mask flag, restored on
# every exit path (same global-snapshot pattern as the armature visibility).
_original_paint_mask_vertex = None
_paint_mask_mesh_name = None

_original_show_wireframes = None

_SESSION_MARKER = "__ssp_wp_session"
_WEIGHT_BRUSH_TOOL_IDNAME = "superskin.weight_brush_tool"
LASSO_TOOL_IDNAME = "superskin.weight_select_tool"


# ==============================================================================
# SCENE MODE HELPERS
# ==============================================================================

def _snapshot_paint_mask(obj):
    """Remember the mesh's vertex-selection paint-mask flag so every exit path can restore it
    (the select tool turns it on when it is used)."""
    global _original_paint_mask_vertex, _paint_mask_mesh_name
    mesh = obj.data
    if not hasattr(mesh, "use_paint_mask_vertex"):
        return
    if _paint_mask_mesh_name is None:
        _original_paint_mask_vertex = mesh.use_paint_mask_vertex
        _paint_mask_mesh_name = mesh.name


def _restore_paint_mask():
    global _original_paint_mask_vertex, _paint_mask_mesh_name
    if _paint_mask_mesh_name is None:
        return
    mesh = bpy.data.meshes.get(_paint_mask_mesh_name)
    if mesh is not None and hasattr(mesh, "use_paint_mask_vertex"):
        mesh.use_paint_mask_vertex = bool(_original_paint_mask_vertex)
    _original_paint_mask_vertex = None
    _paint_mask_mesh_name = None


def _save_and_set_gray():
    global _original_edge_wire, _original_wire_edit, _original_vertex
    if _original_edge_wire is not None:
        return
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
    _original_edge_wire = None
    _original_wire_edit = None
    _original_vertex = None


def _hide_bones_overlay():
    """Turn off the Bones overlay in every VIEW_3D viewport."""
    global _original_show_bones
    if _original_show_bones is not None:
        return  # already hidden by an unfinished prior session
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    overlay = area.spaces.active.overlay
                    if _original_show_bones is None:
                        _original_show_bones = overlay.show_bones
                    overlay.show_bones = False
                except Exception:
                    pass


def _restore_bones_overlay():
    """Restore the Bones overlay snapshotted by
    `_hide_bones_overlay()`. No-op if nothing is currently hidden."""
    global _original_show_bones
    if _original_show_bones is None:
        return
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    area.spaces.active.overlay.show_bones = _original_show_bones
                except Exception:
                    pass
    _original_show_bones = None


def _show_wireframe_overlay():
    """Enable the wireframe overlay in every VIEW_3D viewport, snapshotting
    the first space's value for `_restore_wireframe_overlay()`."""
    global _original_show_wireframes
    if _original_show_wireframes is not None:
        return
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    overlay = area.spaces.active.overlay
                    if _original_show_wireframes is None:
                        _original_show_wireframes = overlay.show_wireframes
                    overlay.show_wireframes = True
                except Exception:
                    pass


def _restore_wireframe_overlay():
    global _original_show_wireframes
    if _original_show_wireframes is None:
        return
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    area.spaces.active.overlay.show_wireframes = _original_show_wireframes
                except Exception:
                    pass
    _original_show_wireframes = None


def _activate_weight_brush_tool(context):
    """Select the addon's Weight Brush tool unless the remembered Weight Paint tool is already
    one of the addon's (Weight Brush or Lasso Select)."""
    workspace = context.workspace
    if workspace is not None:
        tool = workspace.tools.from_space_view3d_mode('PAINT_WEIGHT', create=False)
        if tool is not None and tool.idname in (_WEIGHT_BRUSH_TOOL_IDNAME, LASSO_TOOL_IDNAME):
            return
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None:
                continue
            try:
                with context.temp_override(window=window, area=area, region=region):
                    bpy.ops.wm.tool_set_by_id(name=_WEIGHT_BRUSH_TOOL_IDNAME)
                return
            except Exception:
                continue


# ==============================================================================
# ENTER / EXIT / TOGGLE WEIGHT PAINT SESSION
# ==============================================================================

def _load_active_layer_to_temp_vgs(obj):
    """Create the active Layer's temp VGs (the paint surface native brushes paint on)."""
    if has_temp_vgs(obj):
        delete_temp_vgs(obj)
    storage = LayerStorageService(obj.data)
    if not storage.has_layer_system():
        return
    active_idx = storage.get_active_layer_index()
    _, id_to_bone = get_unified_mapping(obj)
    load_layer_to_temp_vgs(obj, storage.read_layer_dict(active_idx),
                           storage.read_mask_dict(active_idx), active_idx, id_to_bone,
                           layer_mask_default(storage, active_idx))


def _resolve_edit_target_mesh_readonly(context):
    """Read-only mirror of `_enter_edit_mode()`'s target_mesh resolution, used only by."""
    mesh = get_effective_mesh(context)
    if mesh is not None:
        return mesh

    return None


def _enter_edit_mode(op, context):
    """Shared body for entering the Weight Paint session."""
    CoreFacade.debug_log(
        "temp_vg",
        f"_enter_edit_mode() ENTRY: active_object={getattr(context.active_object, 'name', None)!r} "
        f"active_object.type={getattr(context.active_object, 'type', None)!r} "
        f"active_object.mode={getattr(context.active_object, 'mode', None)!r}",
    )

    target_mesh = None

    explicit_mesh_name = getattr(op, 'mesh_name', '')
    if explicit_mesh_name:
        candidate = context.view_layer.objects.get(explicit_mesh_name)
        if candidate is not None and candidate.type == 'MESH':
            target_mesh = candidate

    if not target_mesh:
        target_mesh = get_effective_mesh(context)

    if not target_mesh:
        CoreFacade.debug_log("temp_vg", "_enter_edit_mode() ABORT: no target_mesh resolved")
        op.report({'ERROR'}, "No Mesh or Armature found to enter Edit Layer Weight.")
        return {'CANCELLED'}

    if not any(m.type == 'ARMATURE' and m.object is not None for m in target_mesh.modifiers):
        op.report({'WARNING'}, f"'{target_mesh.name}' has no Armature bound; bind it before editing weights.")
        return {'CANCELLED'}

    CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() target_mesh={target_mesh.name!r}")

    for _mod in target_mesh.modifiers:
        if _mod.type == 'ARMATURE' and _mod.object is not None and not _mod.show_viewport:
            _mod.show_viewport = True

    # ── Ensure the target can become the active object ──────────────
    if context.active_object != target_mesh:
        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        target_mesh.hide_select = False
        target_mesh.hide_viewport = False
        target_mesh.hide_set(False)
        context.view_layer.objects.active = target_mesh
        target_mesh.select_set(True)
        if context.active_object != target_mesh:
            CoreFacade.debug_log(
                "temp_vg",
                f"_enter_edit_mode() ABORT: could not make {target_mesh.name!r} the active "
                f"object — context.active_object is now {getattr(context.active_object, 'name', None)!r}",
            )
            op.report({'ERROR'}, f"Cannot set '{target_mesh.name}' as active object (it may be on a hidden collection or linked).")
            return {'CANCELLED'}

    obj = target_mesh

    if obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    try:
        if native_weight_guard.has_native_mismatch(obj):
            resolution = getattr(op, 'resolution', 'KEEP_SSP')
            if resolution == 'KEEP_NATIVE':
                native_weight_guard.import_native_into_active_layer(context, obj)
            else:
                CoreFacade(context).finish(color_only=False)
            native_weight_guard.record_flatten_signature(obj)
    except Exception:
        pass

    try:
        CoreFacade(context).heal_topology_if_needed()
    except Exception as exc:
        CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() heal_topology_if_needed() raised: {exc!r}")

    try:
        BoneIdentityService(context).backfill_and_scan()
    except Exception as exc:
        CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() backfill_and_scan() raised: {exc!r}")

    if not _has_layer_system(obj):
        CoreFacade(context).init_layer_system()
        _utils.sync_layers_to_ui_collection(obj)

    saved = context.scene.get(f"mw_saved_selection_{obj.name}", [])
    if saved:
        saved_set = set(saved)
        for v in obj.data.vertices:
            v.select = v.index in saved_set

    scene = context.scene
    prev_transaction = scene.superskin_internal_transaction
    scene.superskin_internal_transaction = True
    try:
        _load_active_layer_to_temp_vgs(obj)
        bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
    finally:
        scene.superskin_internal_transaction = prev_transaction

    if obj.mode != 'WEIGHT_PAINT':
        op.report({'ERROR'}, "Could not enter Weight Paint Mode.")
        return {'CANCELLED'}

    _snapshot_paint_mask(obj)
    obj[_SESSION_MARKER] = True

    _restore_addon_ui_for_edit_weight(context, obj)
    _activate_weight_brush_tool(context)

    CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() COMPLETE: obj={obj.name!r} obj.mode={obj.mode!r}")

    return {'FINISHED'}


def _restore_addon_ui_for_edit_weight(context, obj):
    """Restore the addon's own Edit Layer Weight UI to match a Weight Paint session that
    already has (or should have) __ssp_* temp VGs present."""
    _save_and_set_gray()
    _hide_bones_overlay()
    _show_wireframe_overlay()

    # Read directly off storage.active_is_mask (the actual source of truth)
    # rather than scene.superskin_is_mask_mode, since the full sync below is deferred.
    is_mask = bool(obj.superskin_storage.active_is_mask)

    def _sync_active_bone_deferred():
        _ctx = bpy.context
        if _ctx and _ctx.active_object == obj and obj.mode == 'WEIGHT_PAINT':
            try:
                CoreFacade(_ctx).apply_active_bone()
                CoreFacade.debug_log("temp_vg", f"_restore_addon_ui_for_edit_weight() deferred apply_active_bone() done for {obj.name!r}")
            except Exception as exc:
                CoreFacade.debug_log("temp_vg", f"_restore_addon_ui_for_edit_weight() deferred apply_active_bone() raised: {exc!r}")
        else:
            CoreFacade.debug_log(
                "temp_vg",
                f"_restore_addon_ui_for_edit_weight() deferred sync skipped: active_object="
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

    context.window_manager.superskin_active_interface = 'SKINNING'


class OBJECT_OT_mw_enter_edit_mode(bpy.types.Operator):
    bl_idname = "object.mw_enter_edit_mode"
    bl_label = "Enter Edit Mode"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return _enter_edit_mode(self, context)


def _exit_edit_mode(op, context, *, keep_panel_open: bool = False, push_undo_checkpoint: bool = True):
    """Shared body for exiting the Weight Paint session."""
    obj = context.active_object
    if not _is_valid_mesh(obj):
        return {'CANCELLED'}

    _restore_original_colors()
    _restore_bones_overlay()
    _restore_wireframe_overlay()
    _restore_paint_mask()

    scene = context.scene
    prev_transaction = scene.superskin_internal_transaction
    scene.superskin_internal_transaction = True
    try:
        try:
            CoreFacade(context).set_visualizer_mode('CLEAR')
        except Exception:
            pass
        try:
            deform_overlay.hide()
        except Exception:
            pass
        try:
            force_hide_brush_hover()
        except Exception:
            pass
        try:
            CoreFacade.clear_all_hud_slots()
        except Exception:
            pass

        try:
            CoreFacade(context).pull_paint_to_storage()
        except Exception as exc:
            print(f"[SuperSkinPro] exit: pulling paint into storage failed: {exc}")

        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        try:
            if has_temp_vgs(obj):
                delete_temp_vgs(obj)
        except Exception:
            pass
        try:
            native_weight_guard.record_flatten_signature(obj)
        except Exception:
            pass
        if _SESSION_MARKER in obj:
            del obj[_SESSION_MARKER]
    finally:
        scene.superskin_internal_transaction = prev_transaction

    selected_indices = [v.index for v in obj.data.vertices if v.select]
    context.scene[f"mw_saved_selection_{obj.name}"] = selected_indices

    if not keep_panel_open:
        _utils.force_close_tab()

    context.window_manager.superskin_active_interface = 'LAYER'

    if push_undo_checkpoint:
        try:
            bpy.ops.ed.undo_push(message="Exit Edit Layer Weight")
        except Exception:
            pass

    return {'FINISHED'}


def _exit_and_recomposite(op, context):
    """Exit the session, then recomposite so the real deform VGs carry the
    strokes just pulled into storage."""
    result = _exit_edit_mode(op, context)
    if result == {'FINISHED'}:
        try:
            CoreFacade(context).finish(color_only=False)
        except Exception as exc:
            print(f"[SuperSkinPro] Exit: finish() failed: {exc}")
    return result


class OBJECT_OT_mw_exit_edit_mode(bpy.types.Operator):
    bl_idname = "object.mw_exit_edit_mode"
    bl_label = "Exit Edit Mode"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return _exit_and_recomposite(self, context)


class OBJECT_OT_mw_toggle_edit_mode(bpy.types.Operator):
    bl_idname = "object.mw_toggle_edit_mode"
    bl_label = "Toggle Edit Mode"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if obj and obj.mode == 'WEIGHT_PAINT':
            return _exit_and_recomposite(self, context)
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

        armature = get_effective_armature(context)

        if armature is None or armature.name not in context.view_layer.objects:
            self.report({'WARNING'}, "No previous Armature found in cache or modifiers.")
            return {'CANCELLED'}

        if obj and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT':
            # Same shared exit path Save Weights uses, so viewport state is
            # restored before switching to Pose Mode.
            result = _exit_edit_mode(self, context, keep_panel_open=True)
            if result != {'FINISHED'}:
                return result
            try:
                CoreFacade(context).finish(color_only=False)
            except Exception as exc:
                print(f"[SuperSkinPro] Save & Enter Pose Mode: finish() failed: {exc}")

        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        bpy.ops.object.select_all(action='DESELECT')
        context.view_layer.objects.active = armature
        armature.select_set(True)

        bpy.ops.object.mode_set(mode='POSE')

        # Deliberately does not touch sidebar panel state — Pose Mode
        # entry/exit should have zero opinion on panel visibility.
        return {'FINISHED'}


class OBJECT_OT_mw_popup_main_panel(bpy.types.Operator):
    """Toggle the SuperSkinPro sidebar panel without switching scene mode or entering Edit
    Layer Weight."""
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
# MODE-GATE OPERATORS  (UI buttons "Edit Layer Weight" / "Save Weights")
# ==============================================================================

class SUPERSKIN_OT_enter_layer_edit(bpy.types.Operator):
    """Enter Weight Paint Mode and prepare the active layer for weight painting."""
    bl_idname = "superskin.enter_layer_edit"
    bl_label = "Edit"
    bl_description = "Enter Weight Paint Mode to paint weights on the active layer"
    bl_options = {'REGISTER', 'UNDO'}

    resolution: bpy.props.EnumProperty(
        name="Resolution",
        description="Which side wins when native Blender edits and SuperSkinPro's stored weights disagree",
        items=(
            ('KEEP_SSP', "Keep SuperSkinPro Weights", "Discard the native edits; continue with this layer's stored weights"),
            ('KEEP_NATIVE', "Keep Blender Native Weights", "Import the mesh's current native Vertex Group weights into the active layer"),
        ),
        default='KEEP_SSP',
    )

    mesh_name: bpy.props.StringProperty(
        name="Target Mesh",
        description="Explicit mesh to enter Edit Layer Weight on, overriding the "
                     "active-object resolution below. Set by callers that already "
                     "know exactly which mesh the user intends (e.g. layer_viewer's "
                     "Mesh dropdown). Empty string preserves the default resolution "
                     "chain for callers that don't set it.",
        default="",
    )

    def invoke(self, context, event):
        self.resolution = 'KEEP_SSP'
        return self.execute(context)

    @classmethod
    def poll(cls, context):
        target_mesh = _resolve_edit_target_mesh_readonly(context)
        return bool(
            target_mesh is not None
            and context.window_manager.superskin_active_interface == 'LAYER'
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
        return bool(obj and obj.type == 'MESH' and context.mode == 'PAINT_WEIGHT')

    def execute(self, context):
        result = _exit_edit_mode(self, context, keep_panel_open=True, push_undo_checkpoint=False)
        if result == {'FINISHED'}:
            try:
                CoreFacade(context).finish(color_only=False)
            except Exception as exc:
                print(f"[SuperSkinPro] Save Weights: finish() failed: {exc}")
        return result


# ==============================================================================
# UNGUARDED-EXIT GUARD -- restores viewport state when the user leaves Weight
# Paint Mode by any route other than this addon's own exit operators (Tab, a
# mode dropdown, a keymap). No data bake is needed: every write already lands
# in ss_layer_N and the real deform VGs as it happens.
# ==============================================================================

_mode_guard_last_mode: dict = {}  # {obj_name: mode_string}


@bpy.app.handlers.persistent
def _superskin_auto_save_guard(scene, depsgraph):
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

    if (last_mode == 'PAINT_WEIGHT'
            and current_mode != 'PAINT_WEIGHT'
            and not scene.superskin_internal_transaction
            and _SESSION_MARKER in obj):
        ctx.window_manager.superskin_active_interface = 'LAYER'
        _schedule_unguarded_exit_cleanup(obj.name)


def _schedule_unguarded_exit_cleanup(obj_name: str) -> None:
    # Runs from a timer, where context.space_data is not reliably populated,
    # so it only touches state restorable without a space.
    def _cleanup():
        _ctx = bpy.context
        if not _ctx:
            return None
        _obj = _ctx.view_layer.objects.get(obj_name)
        if not (_obj and _obj.type == 'MESH'):
            return None
        if _obj.mode == 'WEIGHT_PAINT':
            return None

        _restore_original_colors()
        _restore_bones_overlay()
        _restore_wireframe_overlay()
        _restore_paint_mask()
        try:
            if has_temp_vgs(_obj):
                _prev = _ctx.view_layer.objects.active
                if _prev != _obj:
                    _ctx.view_layer.objects.active = _obj
                facade = CoreFacade(_ctx)
                facade.pull_paint_to_storage()
                delete_temp_vgs(_obj)
                if _obj.mode == 'OBJECT':
                    facade.finish(color_only=False)
        except Exception as exc:
            print(f"[SuperSkinPro] unguarded exit cleanup failed: {exc}")
        try:
            CoreFacade(_ctx).set_visualizer_mode('CLEAR')
        except Exception:
            pass
        try:
            deform_overlay.hide()
        except Exception:
            pass
        try:
            force_hide_brush_hover()
        except Exception:
            pass
        try:
            CoreFacade.clear_all_hud_slots()
        except Exception:
            pass
        try:
            native_weight_guard.record_flatten_signature(_obj)
        except Exception:
            pass
        if _SESSION_MARKER in _obj:
            del _obj[_SESSION_MARKER]
        return None

    bpy.app.timers.register(_cleanup, first_interval=0.0)


# ==============================================================================
# UNDO/REDO UI RESTORE -- if Ctrl+Z / Ctrl+Shift+Z lands back inside a Weight
# Paint session (marked by _SESSION_MARKER on the object), re-open this
# addon's own UI. Blender's undo/redo only restores mesh/object data, never
# this addon's WindowManager-side interface state.
# ==============================================================================

@bpy.app.handlers.persistent
def _superskin_undo_redo_restore_ui(*_):
    ctx = bpy.context
    if not ctx:
        return
    obj = ctx.active_object
    if not (obj and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'):
        return
    if _SESSION_MARKER not in obj:
        return
    if ctx.window_manager.superskin_active_interface == 'SKINNING':
        return
    try:
        _restore_addon_ui_for_edit_weight(ctx, obj)
    except Exception:
        pass


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
    bpy.app.handlers.undo_post.append(_superskin_undo_redo_restore_ui)
    bpy.app.handlers.redo_post.append(_superskin_undo_redo_restore_ui)


def unregister():
    try:
        bpy.app.handlers.undo_post.remove(_superskin_undo_redo_restore_ui)
    except Exception:
        pass
    try:
        bpy.app.handlers.redo_post.remove(_superskin_undo_redo_restore_ui)
    except Exception:
        pass
    try:
        bpy.app.handlers.depsgraph_update_post.remove(_superskin_auto_save_guard)
    except Exception:
        pass
    _mode_guard_last_mode.clear()
    _restore_original_colors()
    _restore_bones_overlay()
    _restore_wireframe_overlay()
    _restore_paint_mask()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
