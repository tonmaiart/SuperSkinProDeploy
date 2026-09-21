"""Syncs the shared HUD stack with ``obj.superskin_storage.active_is_mask`` (Mask editing
entered via the."""

import bpy

_draw_handle = None
_last_state = None  # None (no line) | 'MASK' | 'BONE'

_HUD_MASK_LABEL = "Edit Layer Mask"
_HUD_MASK_COLOR = (1.0, 0.8, 0.0, 1.0)  # yellow, matching bone_picker's HUD line
_HUD_BONE_LABEL = "Edit Bone"
_HUD_BONE_COLOR = (1.0, 0.8, 0.0, 1.0)  # yellow, matching bone_picker's HUD line
_OWNER_ID = "deform_bone_viewer"
_HUD_SLOT = 0


def _edit_state(obj):
    """Return 'MASK', 'BONE', or None (not in Edit Layer Weight at all)."""
    if not obj or obj.type != 'MESH' or obj.mode != 'WEIGHT_PAINT':
        return None
    storage = getattr(obj, "superskin_storage", None)
    return 'MASK' if (storage and storage.active_is_mask) else 'BONE'


def _draw_callback():
    global _last_state
    state = _edit_state(bpy.context.active_object)
    if state == _last_state:
        return

    from ....core.facade import CoreFacade
    if state == 'MASK':
        from ....interface.utils.icons import get_layer_mask_icon_texture
        CoreFacade.request_hud_slot(
            _OWNER_ID, _HUD_MASK_LABEL, slot=_HUD_SLOT, color=_HUD_MASK_COLOR,
            icon_texture=get_layer_mask_icon_texture()
        )
    elif state == 'BONE':
        from ....interface.utils.icons import get_bone_icon_texture
        CoreFacade.request_hud_slot(
            _OWNER_ID, _HUD_BONE_LABEL, slot=_HUD_SLOT, color=_HUD_BONE_COLOR,
            icon_texture=get_bone_icon_texture()
        )
    else:
        CoreFacade.release_hud_slot(_OWNER_ID)
    _last_state = state


def register():
    global _draw_handle
    if _draw_handle is not None:
        return
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_callback, (), 'WINDOW', 'POST_PIXEL'
    )


def unregister():
    global _draw_handle, _last_state
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if _last_state is not None:
        from ....core.facade import CoreFacade
        CoreFacade.release_hud_slot(_OWNER_ID)
    _last_state = None
