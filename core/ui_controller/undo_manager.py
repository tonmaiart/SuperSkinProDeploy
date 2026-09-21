"""LayerUndoManager — thin undo bridge for SuperSkinPro.

Weight data lives in ss_layer_N / the real deform vertex groups, so Blender's
own undo restores it. This module only needs to:

1. Set the _undo_restore_in_progress flag around an undo so ShaderManager
   doesn't mistake the restore for a real mode change.
2. After undo/redo, resync the Layer list UI collection from the restored
   storage, and trigger a ShaderManager redraw.
3. Expose skin_transaction() for boundary-guarding weight/layer ops.
"""

import bpy
import functools

from ...core_subsystems.context_selection_service import ContextSelectionService as _CSS
from ..shaders.shader_manager import ShaderManager


def skin_transaction(*, color_only: bool = False, check_mask_gaps: bool = False):
    """Boundary guard for operations that mutate layer weights or structure.

    Manages the superskin_internal_transaction flag and guarantees
    ctrl._finish() is called after the wrapped function completes.

    Args:
        color_only: Passed to ctrl._finish(). True for weight-paint ops
            where mesh topology is unchanged; False for structural layer ops.
        check_mask_gaps: When True, calls ctrl.check_for_mask_gaps() after
            _finish(). Use for layer-structure ops that may create coverage
            gaps (move, toggle_visible).

    Conventions:
        - The wrapped function must receive a UIController as args[0] (ctrl).
        - Do NOT apply to functions that call switch_to_layer() internally;
          those handle their own redraw and this decorator would double-flush.
        - Do NOT apply to modal() callbacks; wrap only invoke/execute instead.
    """
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
                if ctrl is not None and hasattr(ctrl, '_finish'):
                    ctrl._finish(color_only=color_only)
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


@bpy.app.handlers.persistent
def _on_undo_pre(*_):
    _CSS.set_undo_restore_in_progress(True)


@bpy.app.handlers.persistent
def _on_undo_post(*_):
    _CSS.reset_undo_flag()
    _sync_after_undo()


@bpy.app.handlers.persistent
def _on_redo_post(*_):
    _CSS.reset_undo_flag()
    _sync_after_undo()


def _sync_after_undo():
    """Resync the Layer list UI collection from restored storage, then redraw."""
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
    bpy.app.handlers.redo_post.append(_on_redo_post)


def unregister():
    for handler_list, fn in (
        (bpy.app.handlers.undo_pre, _on_undo_pre),
        (bpy.app.handlers.undo_post, _on_undo_post),
        (bpy.app.handlers.redo_post, _on_redo_post),
    ):
        try:
            handler_list.remove(fn)
        except ValueError:
            pass
    _CSS.reset_undo_flag()
