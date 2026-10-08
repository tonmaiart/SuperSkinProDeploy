
import bpy
import functools

from ...core_subsystems.context_selection_service import ContextSelectionService as _CSS
from ..shaders.shader_manager import ShaderManager
from ..layer_storage.temp_vg_bridge import SESSION_MARKER


def skin_transaction(*, color_only: bool = False, check_mask_gaps: bool = False):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            ctrl = args[0] if args else None
            scene = None
            try:
                scene = ctrl.ctx.scene
            except Exception:
                try:
                    scene = bpy.context.scene
                except Exception:
                    pass

            was_suppressing = getattr(scene, "superskin_internal_transaction", False) if scene else False
            if scene is not None:
                try:
                    scene.superskin_internal_transaction = True
                except Exception:
                    pass

            try:
                result = func(*args, **kwargs)
                if ctrl is not None and hasattr(ctrl, 'finish'):
                    ctrl.finish(color_only=color_only)
                    if check_mask_gaps and hasattr(ctrl, 'check_for_mask_gaps'):
                        ctrl.check_for_mask_gaps()
                return result
            finally:
                if scene is not None:
                    try:
                        scene.superskin_internal_transaction = was_suppressing
                    except Exception:
                        pass
        return wrapper
    return decorator



_bone_selection_snapshot = None


def _in_weight_session(obj) -> bool:
    return bool(obj and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'
                and SESSION_MARKER in obj)


def _vg_name_at(obj, index: int) -> str:
    if 0 <= index < len(obj.vertex_groups):
        return obj.vertex_groups[index].name
    return ""


def _snapshot_bone_selection():
    global _bone_selection_snapshot
    _bone_selection_snapshot = None
    try:
        obj = bpy.context.active_object
        if not _in_weight_session(obj):
            return
        storage = obj.superskin_storage
        history = []
        for token in storage.selection_history.split(","):
            token = token.strip()
            if token.isdigit():
                name = _vg_name_at(obj, int(token))
                if name:
                    history.append(name)
        rows = obj.superskin_bones_collection
        row_idx = obj.superskin_bones_idx
        _bone_selection_snapshot = {
            "object": obj.name,
            "active_bone_name": storage.active_bone_name,
            "active_orphan_name": storage.active_orphan_name,
            "active_is_mask": storage.active_is_mask,
            "selected_names": storage.selected_names,
            "history": history,
            "native_active": _vg_name_at(obj, obj.vertex_groups.active_index),
            "row_name": rows[row_idx].name if 0 <= row_idx < len(rows) else "",
        }
    except Exception:
        _bone_selection_snapshot = None


def _restore_bone_selection():
    global _bone_selection_snapshot
    snap, _bone_selection_snapshot = _bone_selection_snapshot, None
    if snap is None:
        return
    try:
        context = bpy.context
        obj = context.active_object
        if not _in_weight_session(obj) or obj.name != snap["object"]:
            return
        vgs = obj.vertex_groups
        is_mask = snap["active_is_mask"]
        orphan = snap["active_orphan_name"]
        bone = snap["active_bone_name"]
        if not is_mask and not orphan and bone and vgs.get(bone) is None:
            return

        storage = obj.superskin_storage
        storage.active_bone_name = bone
        storage.active_orphan_name = orphan
        storage.active_is_mask = is_mask
        pool = [n for n in snap["selected_names"].split(",") if n and vgs.get(n) is not None]
        storage.selected_names = "," + "".join(n + "," for n in pool)
        storage.selection_history = ",".join(
            str(vgs[n].index) for n in snap["history"] if vgs.get(n) is not None
        )

        if orphan:
            storage.last_clicked_index = -1
            native = vgs.get(snap["native_active"])
            if native is not None:
                vgs.active_index = native.index
        else:
            from ..facade import CoreFacade
            CoreFacade(context).apply_active_bone()

        rows = obj.superskin_bones_collection
        for i, row in enumerate(rows):
            if row.name == snap["row_name"]:
                obj.superskin_bones_idx = i
                break
    except Exception:
        pass


@bpy.app.handlers.persistent
def _on_undo_pre(*_):
    _CSS.set_undo_restore_in_progress(True)
    _snapshot_bone_selection()


@bpy.app.handlers.persistent
def _on_redo_pre(*_):
    _snapshot_bone_selection()


@bpy.app.handlers.persistent
def _on_undo_post(*_):
    _CSS.reset_undo_flag()
    _sync_after_undo()


@bpy.app.handlers.persistent
def _on_redo_post(*_):
    _CSS.reset_undo_flag()
    _sync_after_undo()


def _sync_after_undo():
    _restore_bone_selection()
    try:
        obj = bpy.context.active_object
        if obj and obj.type == 'MESH':
            from ...interface.utils.utils import sync_layers_to_ui_collection
            sync_layers_to_ui_collection(obj)
    except Exception:
        pass
    try:
        ShaderManager().invalidate_and_redraw()
    except Exception:
        pass


def register():
    bpy.app.handlers.undo_pre.append(_on_undo_pre)
    bpy.app.handlers.undo_post.append(_on_undo_post)
    bpy.app.handlers.redo_pre.append(_on_redo_pre)
    bpy.app.handlers.redo_post.append(_on_redo_post)


def unregister():
    for handler_list, fn in (
        (bpy.app.handlers.undo_pre, _on_undo_pre),
        (bpy.app.handlers.undo_post, _on_undo_post),
        (bpy.app.handlers.redo_pre, _on_redo_pre),
        (bpy.app.handlers.redo_post, _on_redo_post),
    ):
        try:
            handler_list.remove(fn)
        except ValueError:
            pass
    _CSS.reset_undo_flag()
