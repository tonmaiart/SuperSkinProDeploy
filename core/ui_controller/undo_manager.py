"""LayerUndoManager — thin undo bridge for SuperSkinPro.

The actual undo mechanism is Blender's native BMesh undo via Temporary
Vertex Groups (see core/layer_storage/temp_vg_bridge.py). This module
only needs to:

1. Set _undo_restore_in_progress flag so ShaderManager doesn't mistake
   memfile restore's transient EDIT→OBJECT→EDIT bounce for a real mode exit.
2. On undo_post/redo_post: read __ssp_meta to sync active layer in UI
   (undo of a layer switch must restore the correct layer in the panel).
3. Trigger ShaderManager redraw after undo.
4. Expose skin_transaction() decorator for boundary-guarding weight/layer ops.

All parallel stack, checksum, and snapshot logic has been removed.
Blender handles everything.
"""

import bpy
import functools

# Converted from absolute 'SuperSkinPro.core_subsystems...' to relative import.
from ...core_subsystems.context_selection_service import ContextSelectionService as _CSS

# Hoisted from _sync_after_undo / _sync_active_layer_from_meta —
# converted from absolute 'SuperSkinPro.*' imports to relative imports.
from ..shaders.shader_manager import ShaderManager
from ..layer_storage.temp_vg_bridge import get_active_layer_from_meta, bump_pool_epoch
from ..layer_storage.storage_service import LayerStorageService
# sync_layers_to_ui_collection kept function-scoped in _sync_active_layer_from_meta —
# hoisting it triggers a circular import: ui.utils is still initialising when
# undo_manager is loaded through the core → preferences → features chain.


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
    """After Blender restores temp VGs, sync active layer index from __ssp_meta,
    the Deform Bones list's highlighted row from storage.last_clicked_index,
    and invalidate the multi-select pool's draw-time read cache.

    Hoisted imports: ShaderManager.
    """
    try:
        obj = bpy.context.active_object
        if obj and obj.type == 'MESH' and obj.mode == 'EDIT':
            _sync_active_layer_from_meta(obj)
            _sync_active_bone_after_undo(obj)
            # __ssp_pool's weight values are genuinely BMesh-undo-tracked
            # (unlike storage.selected_names), so no resync is needed here --
            # only the read cache (read_pool_names_from_bm(), keyed by an
            # epoch counter) needs invalidating, since Blender's own undo
            # reverts __ssp_pool directly, bypassing the write functions
            # that normally bump it.
            bump_pool_epoch(obj)
    except Exception:
        pass
    try:
        ShaderManager().invalidate_and_redraw()
    except Exception:
        pass


def _sync_active_bone_after_undo(obj):
    """Resync storage.last_clicked_index and superskin_bones_idx from
    obj.vertex_groups.active_index after undo/redo of a bone pick.

    obj.vertex_groups.active_index is a native RNA field -- Blender's
    Edit-Mode lightweight undo tracks it and reverts it correctly.
    storage.last_clicked_index and superskin_bones_idx are both plain
    custom IntProperties (internally just ID Properties, same untracked
    category docs/bug-history/0016 already established for ss_layer_N
    before that redesign) -- Edit-Mode undo does not revert them, so they
    stay pointed at whatever was active right before the undo. Both the
    Deform Bones list highlight (superskin_bones_idx) and the bone_picker
    viewport overlay's active-bone color (deform_overlay.py reads
    storage.last_clicked_index directly at draw time) depend on these
    stale values, which is why both stayed stuck on the pre-undo bone
    while the native Object Data Properties > Vertex Groups panel (driven
    by vertex_groups.active_index) updated correctly on its own.

    In Edit Mode, apply_active_bone() (layer_crud.py) does NOT point
    vertex_groups.active_index at the real bone's own vertex group --
    it repoints it at that bone's __ssp_<bone_id> TEMP vertex group
    instead (so the built-in Vertex Group Weight overlay renders live
    edits). So the value that correctly survives undo is a temp-VG list
    *position*, not the real bone's own vg_index storage.last_clicked_index
    needs -- it has to be translated back through the __ssp_<bone_id>
    naming convention via get_unified_mapping()'s id_to_bone table before
    it can be used to find the matching superskin_bones_collection row.

    Only handles the plain real-bone case -- skipped while Mask editing is
    active (active_index would point at the internal __ssp_m mask VG,
    which has no bone_id suffix to parse) or when the resolved name has no
    real vertex group (orphan bones use a separate sync path, see
    core/bone_identity/ops.py's _sync_bones_idx_to_orphan). Mirrors
    _sync_active_layer_from_meta()'s job for the active layer index above.
    """
    storage = getattr(obj, "superskin_storage", None)
    if storage is None or storage.active_is_mask:
        return
    active_idx = obj.vertex_groups.active_index
    if not (0 <= active_idx < len(obj.vertex_groups)):
        return

    bone_name = obj.vertex_groups[active_idx].name
    if bone_name.startswith("__ssp_"):
        try:
            bone_id = int(bone_name[len("__ssp_"):])
        except ValueError:
            return  # __ssp_m (mask) or __ssp_meta -- not a resolvable bone pick
        _, id_to_bone = LayerStorageService(obj.data).get_unified_mapping(obj)
        bone_name = id_to_bone.get(bone_id)
        if not bone_name:
            return

    real_vg = obj.vertex_groups.get(bone_name)
    if real_vg is None:
        return  # Orphan bone -- has its own sync path, not this one.
    if storage.last_clicked_index != real_vg.index:
        storage.last_clicked_index = real_vg.index
    for i, item in enumerate(obj.superskin_bones_collection):
        if not item.is_orphan and item.name == bone_name:
            if obj.superskin_bones_idx != i:
                obj.superskin_bones_idx = i
            return


def _sync_active_layer_from_meta(obj):
    """Read __ssp_meta VG custom prop → update active layer index + UI collection.

    Hoisted imports: get_active_layer_from_meta, LayerStorageService.
    """
    layer_idx = get_active_layer_from_meta(obj)
    if layer_idx < 0:
        return

    storage = LayerStorageService(obj.data)
    if storage.get_active_layer_index() == layer_idx:
        return

    storage.set_active_layer_index(layer_idx)

    try:
        from ...interface.utils.utils import sync_layers_to_ui_collection
        sync_layers_to_ui_collection(obj)
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
