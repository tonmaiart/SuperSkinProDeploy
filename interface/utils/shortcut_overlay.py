"""Centralized shortcut-overlay HUD."""

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader

_draw_handle = None
_keymaps = []
_overlay_state = {"visible": False}  # off by default on addon start

_HEADER_SIZE = 13
_LINE_SIZE = 12
_LINE_HEIGHT = 17
_HEADER_HEIGHT = 20
_DOMAIN_GAP = 10
_COLUMN_GAP = 14
_LEFT_MARGIN = 90  # clears Blender's native Toolbar on the left edge

_HEADER_COLOR = (1.0, 1.0, 1.0, 1.0)
_KEY_COLOR = (1.0, 0.85, 0.15, 1.0)
_MODE_COLOR = _KEY_COLOR  # "Hold"/"Toggle" tag matches the shortcut-key color
_LABEL_COLOR = (0.9, 0.9, 0.9, 0.9)
_ACTIVE_LABEL_COLOR = (0.5, 1.0, 0.6, 1.0)
_SUB_INDENT = 14

_HINT_TEXT = "Alt+4 to Show Shortcut"
_HINT_SIZE = 12

_STROKE_COLOR = (0.0, 0.0, 0.0, 0.95)
_STROKE_WIDTH = 1.35
_STROKE_OFFSETS = (
    (-1, -1), (0, -1), (1, -1),
    (-1, 0), (1, 0),
    (-1, 1), (0, 1), (1, 1),
)

_BG_COLOR = (0.03, 0.03, 0.03)
_BG_ALPHA = 0.65
_BG_PAD_X = 14
_BG_PAD_Y = 10
_BG_GLOW_STEPS = 5
_BG_GLOW_SPREAD = 10
_BG_GLOW_ALPHA = 0.10


def _draw_rect(x0, y0, x1, y1, color):
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(
        shader, 'TRI_FAN',
        {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]},
    )
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_backdrop(x0, y0, x1, y1):
    gpu.state.blend_set('ALPHA')
    for step in range(_BG_GLOW_STEPS, 0, -1):
        spread = _BG_GLOW_SPREAD * step / _BG_GLOW_STEPS
        alpha = _BG_GLOW_ALPHA * (1.0 - (step - 1) / _BG_GLOW_STEPS)
        _draw_rect(x0 - spread, y0 - spread, x1 + spread, y1 + spread,
                   (*_BG_COLOR, alpha))
    _draw_rect(x0, y0, x1, y1, (*_BG_COLOR, _BG_ALPHA))
    gpu.state.blend_set('NONE')


def _draw_text(font_id, x, y, text, color):
    if not text:
        return
    for ox, oy in _STROKE_OFFSETS:
        blf.position(font_id, x + ox * _STROKE_WIDTH, y + oy * _STROKE_WIDTH, 0)
        blf.color(font_id, *_STROKE_COLOR)
        blf.draw(font_id, text)

    blf.position(font_id, x, y, 0)
    blf.color(font_id, *color)
    blf.draw(font_id, text)


def _is_active(km) -> bool:
    check = km.get("is_active")
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:
        return False




def _row_key_text(context, km: dict) -> str:
    """The key text to draw for one ``get_keymaps()`` entry."""
    from .keymap_editor import resolve_live_key_text

    default = km.get("key", "")
    source_label = km.get("source_label")
    if not source_label:
        return default
    return resolve_live_key_text(context, source_label, default=default)


def _collect_domains(context):
    from ..registry.register_api import UnifiedRegistry
    from .keymap_editor import category_for

    exts = sorted(UnifiedRegistry.get_all(), key=lambda e: (e.get_priority(), e.get_id()))

    for ext in exts:
        for km in ext.get_keymaps():
            sub_keymaps = km.get("sub_keymaps")
            if sub_keymaps and _is_active(km):
                rows = [
                    (sub.get("mode", ""), _row_key_text(context, sub), sub.get("label", ""), True)
                    for sub in sub_keymaps
                    if sub.get("key") or sub.get("label")
                ]
                if rows:
                    return [(category_for(ext), rows)]

    from .keymap_editor import CATEGORY_ORDER

    grouped: dict[str, list] = {}
    for ext in exts:
        rows = [
            (km.get("mode", ""), _row_key_text(context, km), km.get("label", ""), False)
            for km in ext.get_keymaps()
            if km.get("key") or km.get("label")
        ]
        if rows:
            grouped.setdefault(category_for(ext), []).extend(rows)

    domains = []
    for category in CATEGORY_ORDER:
        rows = grouped.pop(category, None)
        if rows:
            domains.append((category, rows))
    for category in sorted(grouped):
        domains.append((category, grouped[category]))
    return domains


def _draw_shortcut_overlay_callback():
    context = bpy.context
    if not context.space_data or context.space_data.type != 'VIEW_3D':
        return

    from ...core.facade import CoreFacade
    if not CoreFacade.is_editing_weights():
        return

    if not _overlay_state["visible"]:
        font_id = 0
        blf.size(font_id, _HINT_SIZE)
        y = context.region.height // 2
        hint_width = blf.dimensions(font_id, _HINT_TEXT)[0]
        _draw_backdrop(
            _LEFT_MARGIN - _BG_PAD_X, y - _BG_PAD_Y,
            _LEFT_MARGIN + hint_width + _BG_PAD_X, y + _HINT_SIZE + _BG_PAD_Y,
        )
        _draw_text(font_id, _LEFT_MARGIN, y, _HINT_TEXT, _KEY_COLOR)
        return

    domains = _collect_domains(context)
    if not domains:
        return

    font_id = 0

    # Column widths shared across every domain so rows line up throughout
    # the whole vertical list, not just within one domain's own block.
    blf.size(font_id, _LINE_SIZE)
    mode_width = 0.0
    key_width = 0.0
    label_width = 0.0
    for _header, rows in domains:
        for mode, key, label, is_sub in rows:
            mode_width = max(mode_width, blf.dimensions(font_id, mode)[0])
            key_width = max(key_width, blf.dimensions(font_id, key)[0])
            indent = _SUB_INDENT if is_sub else 0
            label_width = max(label_width, blf.dimensions(font_id, label)[0] + indent)

    blf.size(font_id, _HEADER_SIZE)
    header_width = 0.0
    for header, _rows in domains:
        header_width = max(header_width, blf.dimensions(font_id, header)[0])

    x_mode = _LEFT_MARGIN
    x_key = x_mode + mode_width + _COLUMN_GAP if mode_width else x_mode
    x_label = x_key + key_width + _COLUMN_GAP

    content_width = max(header_width, (x_label - _LEFT_MARGIN) + label_width)

    total_height = sum(
        _HEADER_HEIGHT + _LINE_HEIGHT * len(rows) for _h, rows in domains
    ) + _DOMAIN_GAP * (len(domains) - 1)

    region_height = context.region.height
    y = region_height // 2 + total_height // 2

    _draw_backdrop(
        _LEFT_MARGIN - _BG_PAD_X, y - total_height - _BG_PAD_Y,
        _LEFT_MARGIN + content_width + _BG_PAD_X, y + _HEADER_SIZE + _BG_PAD_Y,
    )

    for header, rows in domains:
        blf.size(font_id, _HEADER_SIZE)
        _draw_text(font_id, _LEFT_MARGIN, y, header, _HEADER_COLOR)
        y -= _HEADER_HEIGHT

        blf.size(font_id, _LINE_SIZE)
        for mode, key, label, is_sub in rows:
            indent = _SUB_INDENT if is_sub else 0
            label_color = _ACTIVE_LABEL_COLOR if is_sub else _LABEL_COLOR
            _draw_text(font_id, x_mode + indent, y, mode, _MODE_COLOR)
            _draw_text(font_id, x_key + indent, y, key, _KEY_COLOR)
            _draw_text(font_id, x_label + indent, y, label, label_color)
            y -= _LINE_HEIGHT

        y -= _DOMAIN_GAP


def _tag_redraw_all():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class SUPERSKIN_OT_toggle_shortcut_overlay(bpy.types.Operator):
    """Toggle the shortcut-overlay HUD on/off (Alt+4)."""
    bl_idname = "superskin.toggle_shortcut_overlay"
    bl_label = "Toggle Shortcut Overlay"

    def execute(self, context):
        _overlay_state["visible"] = not _overlay_state["visible"]
        _tag_redraw_all()
        return {'FINISHED'}


def register_overlay():
    global _draw_handle
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_shortcut_overlay_callback, (), 'WINDOW', 'POST_PIXEL'
        )

    bpy.utils.register_class(SUPERSKIN_OT_toggle_shortcut_overlay)

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='Weight Paint', space_type='EMPTY')
        kmi = km.keymap_items.new(
            "superskin.toggle_shortcut_overlay", type='FOUR', value='PRESS', alt=True)
        _keymaps.append((km, kmi))


def unregister_overlay():
    global _draw_handle

    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()

    try:
        bpy.utils.unregister_class(SUPERSKIN_OT_toggle_shortcut_overlay)
    except Exception:
        pass

    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        except Exception:
            pass
        _draw_handle = None
