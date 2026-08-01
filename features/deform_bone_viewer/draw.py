"""Bottom-center "Editing Mask" HUD, shown while
``obj.superskin_storage.active_is_mask`` is True (Mask editing entered via
the "Mask" toggle button, ``superskin.toggle_mask_mode`` in ``ops.py``).

Same bottom-center blf text treatment as
``features/overlay_color/multi_color_draw.py``'s "MULTI COLOR MODE" HUD and
``features/bone_picker/deform_overlay.py``'s "BONE PICKER" HUD (drop-shadow
pass, then colored pass on top) — just a different label/color so none of
the three are visually confused.

Installed once at register() and left installed for the addon's lifetime,
mirroring ``bone_picker/deform_overlay.py``'s persistent-handler pattern:
the draw callback self-silences whenever Mask editing isn't active, so no
separate show()/hide() calls are needed from ``ops.py`` on toggle.
"""

import bpy
import blf

_draw_handle = None

_HUD_LABEL = "Editing Mask"
_HUD_COLOR = (1.0, 1.0, 0.0, 1.0)


def _draw_hud(context):
    font_id = 0
    blf.size(font_id, 24)
    text_w, _ = blf.dimensions(font_id, _HUD_LABEL)
    region_width = context.region.width
    center_x = max(0, region_width // 2 - int(text_w) // 2)
    y_offset = 45
    blf.position(font_id, center_x + 2, y_offset + 2, 0)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.85)
    blf.draw(font_id, _HUD_LABEL)
    blf.position(font_id, center_x, y_offset, 0)
    blf.color(font_id, *_HUD_COLOR)
    blf.draw(font_id, _HUD_LABEL)


def _draw_callback():
    context = bpy.context
    obj = context.active_object
    if not obj or obj.type != 'MESH' or obj.mode != 'EDIT':
        return

    region = context.region
    if not region:
        return

    storage = getattr(obj, "superskin_storage", None)
    if not storage or not storage.active_is_mask:
        return

    _draw_hud(context)


def register():
    global _draw_handle
    if _draw_handle is not None:
        return
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_callback, (), 'WINDOW', 'POST_PIXEL'
    )


def unregister():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
