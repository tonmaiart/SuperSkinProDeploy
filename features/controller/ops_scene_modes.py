
import json

import bpy
import numpy as np

from ...core.facade import CoreFacade
from ...core.bone_identity import BoneIdentityService
from ...interface.utils.utils import _is_valid_mesh, _has_layer_system

from ...core.layer_storage.temp_vg_bridge import (
    has_temp_vgs, delete_temp_vgs, is_group_layer, load_stored_layer_to_temp_vgs,
)
from ...core.layer_storage.storage_service import LayerStorageService
from ...interface.utils import utils as _utils
from ...interface.utils import viewport_overrides
from ..bone_picker import deform_overlay
from ..weight_apply.public_api import (
    clear_mesh_selection, force_hide_brush_hover, get_active_weight_tool_idname, hide_weight_tools,
    last_weight_tool_idname, read_vertex_select, reset_stale_weight_tool, show_weight_tools,
    write_vertex_select,
)
from ..deform_layer_viewer.layer_viewer.public_api import get_effective_mesh, get_effective_armature
from . import native_weight_guard



_SHOW_OVERLAYS_OWNER = "controller.show_overlays"
_HIDE_BONES_OWNER = "controller.hide_bones"

_original_paint_mask_vertex = None
_paint_mask_mesh_name = None

_SESSION_MARKER = CoreFacade.SESSION_MARKER
_WEIGHT_BRUSH_TOOL_IDNAME = "superskin.weight_brush_tool"
LASSO_TOOL_IDNAME = "superskin.weight_select_tool"

_pose_return_mesh_name = None
_keymaps = []

_SCENE_VIEWPORT_SNAPSHOT_KEY = "__ssp_viewport_snapshot"



def _sync_scene_snapshot():
    scene = bpy.context.scene
    if scene is None:
        return
    if _paint_mask_mesh_name is None:
        if _SCENE_VIEWPORT_SNAPSHOT_KEY in scene:
            del scene[_SCENE_VIEWPORT_SNAPSHOT_KEY]
        return
    scene[_SCENE_VIEWPORT_SNAPSHOT_KEY] = json.dumps({
        "paint_mask_vertex": bool(_original_paint_mask_vertex),
        "paint_mask_mesh_name": _paint_mask_mesh_name or "",
    })


def _snapshot_paint_mask(obj):
    global _original_paint_mask_vertex, _paint_mask_mesh_name
    mesh = obj.data
    if not hasattr(mesh, "use_paint_mask_vertex"):
        return
    if _paint_mask_mesh_name is None:
        _original_paint_mask_vertex = mesh.use_paint_mask_vertex
        _paint_mask_mesh_name = mesh.name
        _sync_scene_snapshot()


def _restore_paint_mask():
    global _original_paint_mask_vertex, _paint_mask_mesh_name
    if _paint_mask_mesh_name is None:
        return
    mesh = bpy.data.meshes.get(_paint_mask_mesh_name)
    if mesh is not None and hasattr(mesh, "use_paint_mask_vertex"):
        mesh.use_paint_mask_vertex = bool(_original_paint_mask_vertex)
    _original_paint_mask_vertex = None
    _paint_mask_mesh_name = None
    _sync_scene_snapshot()


def _apply_viewport_overrides():
    viewport_overrides.apply_view3d(_SHOW_OVERLAYS_OWNER, {"overlay.show_overlays": True}, once=True)
    viewport_overrides.apply_view3d(_HIDE_BONES_OWNER, {"overlay.show_bones": False}, once=True)


def _restore_viewport_overrides():
    viewport_overrides.restore(_SHOW_OVERLAYS_OWNER)
    viewport_overrides.restore(_HIDE_BONES_OWNER)


def _activate_weight_brush_tool(context, idname=None):
    workspace = context.workspace
    if idname is None and workspace is not None:
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
                    bpy.ops.wm.tool_set_by_id(
                        name=idname or last_weight_tool_idname() or _WEIGHT_BRUSH_TOOL_IDNAME)
                return
            except Exception:
                continue



def _load_active_layer_to_temp_vgs(obj):
    if has_temp_vgs(obj):
        delete_temp_vgs(obj)
    storage = LayerStorageService(obj.data)
    if not storage.has_layer_system():
        return
    active_idx = storage.get_active_layer_index()
    if is_group_layer(storage, active_idx):
        obj.superskin_storage.active_is_mask = True
    load_stored_layer_to_temp_vgs(obj, storage, active_idx)


def _resolve_edit_target_mesh_readonly(context):
    mesh = get_effective_mesh(context)
    if mesh is not None:
        return mesh

    return None


def _clear_mesh_selection(mesh):
    clear_mesh_selection(mesh)


def _enter_edit_mode(op, context, tool_idname=None):
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
            if native_weight_guard.vertex_count_dropped(obj):
                if not native_weight_guard.remap_layers_by_position(context, obj):
                    resolution = 'KEEP_NATIVE'
                else:
                    resolution = None
            if resolution == 'KEEP_NATIVE':
                native_weight_guard.import_native_into_active_layer(context, obj)
            elif resolution is not None:
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

    try:
        migrated = CoreFacade(context).migrate_layer_storage()
        if migrated:
            CoreFacade.debug_log("core_pipeline", f"_enter_edit_mode() migrated {migrated} layer blob(s)")
    except Exception as exc:
        CoreFacade.debug_log("core_pipeline", f"_enter_edit_mode() layer migration raised: {exc!r}")

    saved = context.scene.get(f"mw_saved_selection_{obj.name}", [])
    if saved:
        flags = np.zeros(len(obj.data.vertices), dtype=np.bool_)
        idx = np.asarray(list(saved), dtype=np.int64)
        flags[idx[(idx >= 0) & (idx < len(flags))]] = True
        write_vertex_select(obj.data, flags)
    else:
        _clear_mesh_selection(obj.data)

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
    _activate_weight_brush_tool(context, tool_idname)

    CoreFacade.debug_log("temp_vg", f"_enter_edit_mode() COMPLETE: obj={obj.name!r} obj.mode={obj.mode!r}")

    return {'FINISHED'}


def _restore_addon_ui_for_edit_weight(context, obj):
    try:
        show_weight_tools()
    except Exception as exc:
        print(f"[SuperSkinPro] showing weight tools failed: {exc}")
    _apply_viewport_overrides()

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
    obj = context.active_object
    if not _is_valid_mesh(obj):
        return {'CANCELLED'}

    _restore_viewport_overrides()
    _restore_paint_mask()

    scene = context.scene
    prev_transaction = scene.superskin_internal_transaction
    scene.superskin_internal_transaction = True
    try:
        try:
            deform_overlay.hide()
        except Exception:
            pass
        try:
            force_hide_brush_hover()
        except Exception:
            pass
        try:
            hide_weight_tools()
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

    flags = read_vertex_select(obj.data)
    context.scene[f"mw_saved_selection_{obj.name}"] = np.flatnonzero(flags).tolist()

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
    result = _exit_edit_mode(op, context, push_undo_checkpoint=False)
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


def _mesh_bound_to(mesh, armature):
    return any(m.type == 'ARMATURE' and m.object == armature for m in mesh.modifiers)


def _session_mesh(context):
    obj = context.active_object
    if obj and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT' and _SESSION_MARKER in obj:
        return obj
    return None


def _posing_armature(context):
    obj = context.active_object
    if obj and obj.type == 'ARMATURE' and obj.mode == 'POSE':
        return obj
    return None


def _save_and_exit(op, context):
    result = _exit_edit_mode(op, context, keep_panel_open=True, push_undo_checkpoint=False)
    if result == {'FINISHED'}:
        try:
            CoreFacade(context).finish(color_only=False)
        except Exception as exc:
            print(f"[SuperSkinPro] Save Weights: finish() failed: {exc}")
    return result


def _enter_pose(op, context):
    global _pose_return_mesh_name
    armature = get_effective_armature(context)
    mesh = get_effective_mesh(context)
    if armature is None or armature.name not in context.view_layer.objects:
        op.report({'WARNING'}, "No bound Armature found to enter Pose Mode.")
        return {'CANCELLED'}

    if _session_mesh(context) is not None:
        result = _save_and_exit(op, context)
        if result != {'FINISHED'}:
            return result
    if mesh is not None and mesh.type == 'MESH':
        _pose_return_mesh_name = mesh.name

    if context.active_object and context.active_object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    armature.hide_select = False
    if armature.hide_get():
        armature.hide_set(False)
    context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode='POSE')
    context.window_manager.superskin_active_interface = 'POSE'
    return {'FINISHED'}


def _exit_pose(context):
    bpy.ops.object.mode_set(mode='OBJECT')
    context.window_manager.superskin_active_interface = 'LAYER'
    return {'FINISHED'}


def _enter_session(op, context, tool_idname=None):
    armature = _posing_armature(context)
    if armature is not None:
        mesh = None
        name = op.mesh_name or _pose_return_mesh_name
        candidate = context.view_layer.objects.get(name) if name else None
        if candidate is not None and candidate.type == 'MESH' and _mesh_bound_to(candidate, armature):
            mesh = candidate
        if mesh is None:
            mesh = get_effective_mesh(context)
        if mesh is None:
            op.report({'WARNING'}, f"No mesh bound to '{armature.name}' to return to.")
            return {'CANCELLED'}
        bpy.ops.object.mode_set(mode='OBJECT')
        op.mesh_name = mesh.name
    return _enter_edit_mode(op, context, tool_idname)


class OBJECT_OT_mw_toggle_session_pose(bpy.types.Operator):
    """Save your weights and switch to Pose Mode, or go back to weight painting"""
    bl_idname = "object.mw_toggle_session_pose"
    bl_label = "Toggle Pose Mode"
    bl_options = {'REGISTER', 'UNDO'}

    mesh_name: bpy.props.StringProperty(default="", options={'SKIP_SAVE', 'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _session_mesh(context) is not None or _posing_armature(context) is not None

    def execute(self, context):
        if _posing_armature(context) is not None:
            return _enter_session(self, context)
        return _enter_pose(self, context)


_MODE_TOOL_IDNAMES = {'BRUSH': _WEIGHT_BRUSH_TOOL_IDNAME, 'VERTEX': LASSO_TOOL_IDNAME}


class OBJECT_OT_mw_mode_tool(bpy.types.Operator):
    """Switch between the weight painting tools and Pose Mode"""
    bl_idname = "object.mw_mode_tool"
    bl_label = "Mode Tool"
    bl_options = {'UNDO'}

    target: bpy.props.EnumProperty(
        items=[
            ('OBJECT', "Object", "Object Mode"),
            ('BRUSH', "Brush", "Weight Brush"),
            ('VERTEX', "Vertex", "Lasso Select"),
            ('ARMATURE', "Armature", "Pose Mode on the bound Armature"),
        ],
        default='BRUSH',
        options={'SKIP_SAVE'},
    )
    mesh_name: bpy.props.StringProperty(default="", options={'SKIP_SAVE', 'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _posing_armature(context) is not None or get_effective_mesh(context) is not None

    @classmethod
    def description(cls, context, properties):
        if properties.target == 'OBJECT':
            if _session_mesh(context) is not None:
                return "Save weights and switch to Object Mode"
            return "Switch to Object Mode"
        if properties.target == 'ARMATURE':
            if _posing_armature(context) is not None:
                return "Leave Pose Mode"
            return "Save weights and switch to the bound Armature's Pose Mode"
        if (_session_mesh(context) is not None
                and get_active_weight_tool_idname(context) == _MODE_TOOL_IDNAMES[properties.target]):
            return "Save weights and leave Weight Paint Mode"
        label = "Weight Brush" if properties.target == 'BRUSH' else "Lasso Select"
        return f"Paint weights with {label}"

    def execute(self, context):
        if self.target == 'OBJECT':
            if _session_mesh(context) is not None:
                return _save_and_exit(self, context)
            if _posing_armature(context) is not None:
                return _exit_pose(context)
            obj = context.active_object
            if obj is None or obj.mode == 'OBJECT':
                return {'CANCELLED'}
            bpy.ops.object.mode_set(mode='OBJECT')
            context.window_manager.superskin_active_interface = 'LAYER'
            return {'FINISHED'}

        if self.target == 'ARMATURE':
            if _posing_armature(context) is not None:
                return _exit_pose(context)
            return _enter_pose(self, context)

        tool_idname = _MODE_TOOL_IDNAMES[self.target]
        if _session_mesh(context) is not None:
            if get_active_weight_tool_idname(context) == tool_idname:
                return _save_and_exit(self, context)
            bpy.ops.superskin.toggle_weight_brush_tool(tool=self.target)
            return {'FINISHED'}
        return _enter_session(self, context, tool_idname)


class OBJECT_OT_mw_popup_main_panel(bpy.types.Operator):
    """Show or hide the SuperSkinPro panel"""
    bl_idname = "object.mw_popup_main_panel"
    bl_label = "Popup Main Panel"
    bl_options = {'REGISTER'}

    def execute(self, context):
        if _utils.is_super_skin_tab_open():
            _utils.force_close_tab()
        else:
            _utils.force_open_super_skin_tab()
        return {'FINISHED'}



class SUPERSKIN_OT_enter_layer_edit(bpy.types.Operator):
    """Start painting weights on the active layer"""
    bl_idname = "superskin.enter_layer_edit"
    bl_label = "Edit"
    bl_description = "Start painting weights on the active layer"
    bl_options = {'REGISTER', 'UNDO'}

    resolution: bpy.props.EnumProperty(
        name="Resolution",
        description="Which weights to keep when the mesh was edited outside SuperSkinPro",
        items=(
            ('KEEP_SSP', "Keep SuperSkinPro Weights", "Discard the outside edits and keep this layer's saved weights"),
            ('KEEP_NATIVE', "Keep Blender Native Weights", "Bring the mesh's current vertex group weights into the active layer"),
        ),
        default='KEEP_SSP',
    )

    mesh_name: bpy.props.StringProperty(
        name="Target Mesh",
        description="Mesh to edit",
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
            and context.window_manager.superskin_active_interface in {'LAYER', 'POSE'}
        )

    def execute(self, context):
        return _enter_edit_mode(self, context)


class SUPERSKIN_OT_save_weights(bpy.types.Operator):
    """Save your weight changes and go back to Object Mode"""
    bl_idname = "superskin.save_weights"
    bl_label = "Save Weights"
    bl_description = "Save your weight changes and go back to Object Mode"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH' and context.mode == 'PAINT_WEIGHT')

    def execute(self, context):
        return _save_and_exit(self, context)



_mode_guard_last_mode: dict = {}


@bpy.app.handlers.persistent
def _superskin_auto_save_guard(scene, depsgraph):
    ctx = bpy.context
    if not ctx:
        return
    obj = ctx.active_object
    wm = ctx.window_manager
    if (wm.superskin_active_interface == 'POSE'
            and not (obj and obj.type == 'ARMATURE' and obj.mode == 'POSE')):
        wm.superskin_active_interface = 'LAYER'
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
    elif (current_mode == 'PAINT_WEIGHT'
            and last_mode != 'PAINT_WEIGHT'
            and not scene.superskin_internal_transaction
            and _SESSION_MARKER not in obj):
        if _has_layer_system(obj):
            bpy.app.timers.register(
                lambda name=obj.name: _auto_enter_edit_mode_deferred(name), first_interval=0.0)
        else:
            bpy.app.timers.register(_reset_stale_weight_tool_deferred, first_interval=0.0)


def _reset_stale_weight_tool_deferred():
    try:
        reset_stale_weight_tool()
    except Exception:
        pass
    return None


def _auto_enter_edit_mode_deferred(obj_name: str):
    ctx = bpy.context
    if not ctx:
        return None
    obj = ctx.view_layer.objects.get(obj_name)
    if not (obj and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT' and _SESSION_MARKER not in obj):
        return None
    if ctx.view_layer.objects.active != obj:
        ctx.view_layer.objects.active = obj
    try:
        bpy.ops.object.mw_enter_edit_mode()
    except Exception as exc:
        print(f"[SuperSkinPro] auto-enter on native Weight Paint failed: {exc}")
    return None


def _flush_and_clear_session(obj) -> None:
    ctx = bpy.context
    try:
        if has_temp_vgs(obj):
            prev = ctx.view_layer.objects.active
            if prev != obj:
                ctx.view_layer.objects.active = obj
            facade = CoreFacade(ctx)
            facade.pull_paint_to_storage()
            delete_temp_vgs(obj)
            if obj.mode == 'OBJECT':
                facade.finish(color_only=False)
    except Exception as exc:
        print(f"[SuperSkinPro] stale session cleanup failed: {exc}")
    try:
        deform_overlay.hide()
    except Exception:
        pass
    try:
        force_hide_brush_hover()
    except Exception:
        pass
    try:
        hide_weight_tools()
    except Exception:
        pass
    try:
        CoreFacade.clear_all_hud_slots()
    except Exception:
        pass
    try:
        native_weight_guard.record_flatten_signature(obj)
    except Exception:
        pass
    if _SESSION_MARKER in obj:
        del obj[_SESSION_MARKER]


def _schedule_unguarded_exit_cleanup(obj_name: str) -> None:
    def _cleanup():
        _ctx = bpy.context
        if not _ctx:
            return None
        _obj = _ctx.view_layer.objects.get(obj_name)
        if not (_obj and _obj.type == 'MESH'):
            return None
        if _obj.mode == 'WEIGHT_PAINT':
            return None

        _restore_viewport_overrides()
        _restore_paint_mask()
        _flush_and_clear_session(_obj)
        return None

    bpy.app.timers.register(_cleanup, first_interval=0.0)



def _run_load_post_recovery():
    global _original_paint_mask_vertex, _paint_mask_mesh_name

    ctx = bpy.context
    if not ctx:
        return None
    scene = ctx.scene
    if scene is None:
        return None

    viewport_overrides.restore_all()

    raw = scene.get(_SCENE_VIEWPORT_SNAPSHOT_KEY)
    if raw is not None:
        try:
            snap = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            snap = None

        if snap is not None:
            paint_mask_mesh_name = snap.get("paint_mask_mesh_name")
            if paint_mask_mesh_name:
                _original_paint_mask_vertex = snap.get("paint_mask_vertex")
                _paint_mask_mesh_name = paint_mask_mesh_name
                _restore_paint_mask()

        if _SCENE_VIEWPORT_SNAPSHOT_KEY in scene:
            del scene[_SCENE_VIEWPORT_SNAPSHOT_KEY]

    for obj in list(bpy.data.objects):
        if obj.type != 'MESH' or _SESSION_MARKER not in obj:
            continue
        try:
            if obj.mode == 'WEIGHT_PAINT':
                prev = ctx.view_layer.objects.active
                if prev != obj:
                    ctx.view_layer.objects.active = obj
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception as exc:
            print(f"[SuperSkinPro] load_post: could not exit stale Weight Paint on {obj.name!r}: {exc}")
        _flush_and_clear_session(obj)

    ctx.window_manager.superskin_active_interface = 'LAYER'
    return None


@bpy.app.handlers.persistent
def _superskin_load_post_restore(*_):
    bpy.app.timers.register(_run_load_post_recovery, first_interval=0.0)



@bpy.app.handlers.persistent
def _superskin_undo_redo_restore_ui(*_):
    ctx = bpy.context
    if not ctx:
        return
    obj = ctx.active_object
    in_session = bool(obj and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT' and _SESSION_MARKER in obj)

    if in_session:
        if ctx.window_manager.superskin_active_interface != 'SKINNING':
            try:
                _restore_addon_ui_for_edit_weight(ctx, obj)
            except Exception:
                pass
        return

    if ctx.window_manager.superskin_active_interface != 'SKINNING':
        return

    ctx.window_manager.superskin_active_interface = 'LAYER'
    _restore_viewport_overrides()
    _restore_paint_mask()
    try:
        deform_overlay.hide()
    except Exception:
        pass
    try:
        force_hide_brush_hover()
    except Exception:
        pass
    try:
        hide_weight_tools()
    except Exception:
        pass
    try:
        CoreFacade.clear_all_hud_slots()
    except Exception:
        pass



_classes = (
    OBJECT_OT_mw_enter_edit_mode,
    OBJECT_OT_mw_exit_edit_mode,
    OBJECT_OT_mw_toggle_edit_mode,
    OBJECT_OT_mw_toggle_session_pose,
    OBJECT_OT_mw_mode_tool,
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
    bpy.app.handlers.load_post.append(_superskin_load_post_restore)
    viewport_overrides.register()

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        for km_name in ('Weight Paint', 'Pose'):
            km = kc.keymaps.new(name=km_name, space_type='EMPTY')
            kmi = km.keymap_items.new(
                "superskin.toggle_shortcut_hint",
                type='FOUR', value='PRESS', alt=True,
            )
            _keymaps.append((km, kmi, f"Toggle Shortcut Hint ({km_name})"))


def get_registered_keymap_items():
    return list(_keymaps)


def unregister():
    for km, kmi, _label in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _keymaps.clear()
    try:
        bpy.app.handlers.load_post.remove(_superskin_load_post_restore)
    except Exception:
        pass
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
    _restore_viewport_overrides()
    _restore_paint_mask()
    viewport_overrides.unregister()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
