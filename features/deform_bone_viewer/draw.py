"""Syncs the shared HUD stack with
``obj.superskin_storage.active_is_mask`` (Mask editing entered via the
"Mask" toggle button, ``superskin.toggle_mask_mode`` in ``ops.py`` --
but also flippable directly from ``ui.py``'s mask-row selection logic, so
this module cannot simply hook one call site and must keep polling).

Always requests a line on the shared HUD stack (see
``core/facade/README.md``'s "Shared HUD Stack" section) while a mesh is in
Edit Mode, instead of owning a bottom-of-screen ``blf`` draw handler
directly -- the shared stack is what guarantees this label can never
visually overlap ``features/overlay_color/multi_color_draw.py``'s pink
"Multi Color" or ``features/bone_picker/deform_overlay.py``'s yellow
"BONE PICKER" line. Alternates between two states so the HUD reads as one
consistent status line regardless of which one is active, rather than only
appearing for Mask mode and staying silent otherwise. Both lines currently
share bone_picker's yellow HUD color (per a later request to make every HUD
line read as one consistent color):
  - Mask editing on: "Edit Mask Weight" + the same edit-mask icon used on
    the "Mask" toggle button (``interface/utils/icons.py``'s
    ``get_layer_mask_icon_texture()``).
  - Mask editing off (still in Edit Mode): "Edit Bone Weight" + a bone icon
    (``get_bone_icon_texture()``).

Installed once at register() and left installed for the addon's lifetime.
The draw callback itself draws nothing -- it only polls
``active_is_mask``/``obj.mode`` on every redraw and re-requests the slot on
each state edge (None/'MASK'/'BONE'), which is what lets it stay correct
regardless of which code path flipped the flag.
"""

import bpy

_draw_handle = None
_last_state = None  # None (no line) | 'MASK' | 'BONE'

_HUD_MASK_LABEL = "Edit Layer Mask"
_HUD_MASK_COLOR = (1.0, 0.8, 0.0, 1.0)  # yellow, matching bone_picker's HUD line
_HUD_BONE_LABEL = "Edit Bone"
_HUD_BONE_COLOR = (1.0, 0.8, 0.0, 1.0)  # yellow, matching bone_picker's HUD line
_OWNER_ID = "deform_bone_viewer"
_HUD_SLOT = 0  # bottom-most row -- see core/facade/README.md's reserved-slot table


def _edit_state(obj):
    """Return 'MASK', 'BONE', or None (not in Edit Layer Weight at all)."""
    if not obj or obj.type != 'MESH' or obj.mode != 'EDIT':
        return None
    storage = getattr(obj, "superskin_storage", None)
    return 'MASK' if (storage and storage.active_is_mask) else 'BONE'


def _draw_callback():
    global _last_state
    state = _edit_state(bpy.context.active_object)
    if state == _last_state:
        return

    from ...core.facade import CoreFacade
    if state == 'MASK':
        from ...interface.utils.icons import get_layer_mask_icon_texture
        CoreFacade.request_hud_slot(
            _OWNER_ID, _HUD_MASK_LABEL, slot=_HUD_SLOT, color=_HUD_MASK_COLOR,
            icon_texture=get_layer_mask_icon_texture()
        )
    elif state == 'BONE':
        from ...interface.utils.icons import get_bone_icon_texture
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
        from ...core.facade import CoreFacade
        CoreFacade.release_hud_slot(_OWNER_ID)
    _last_state = None
