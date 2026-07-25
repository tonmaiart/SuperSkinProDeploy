"""Weight Brush cursor -- outer (radius) + inner (falloff start) circle,
drawn at the brush's current hit point.

Same GPU draw conventions as `../draw.py`'s gesture HUD: UNIFORM_COLOR
shader, batch_for_shader, POST_PIXEL on SpaceView3D. This module itself
owns no lifecycle logic beyond the plain `show()`/`update()`/`hide()`/
`cleanup()` calls below -- it's driven by TWO independent callers that
never run at the same time:

  - `brush_ops.py`'s `SUPERSKIN_OT_weight_brush` -- while an actual
    stroke is running (LMB held), via its own invoke()/modal()/release.
  - `brush_hover.py`'s persistent background modal -- on hover, whenever
    the Weight Brush tool is active and no stroke is currently running
    (checked via `SUPERSKIN_OT_weight_brush._stroke_active`), so the
    cursor is visible continuously while the tool is selected, not only
    mid-stroke.

Both callers call the same `show()`/`update()`/`hide()` functions here;
neither needs to know the other exists.
"""

import math

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

_draw_handle = None
_active = False

_center_2d = (0.0, 0.0)
_radius_px = 0.0
_falloff_edge_px = 0.0   # inner ring -- where falloff starts (== radius_px when falloff is 0)
_label = ""

_SEGMENTS = 48
_OUTER_COLOR = (1.0, 1.0, 1.0, 0.9)
_INNER_COLOR = (1.0, 1.0, 1.0, 0.35)
_CENTER_COLOR = (1.0, 1.0, 1.0, 0.9)
_TEXT_COLOR = (1.0, 1.0, 1.0, 1.0)


def _circle_points(center, radius):
    cx, cy = center
    return [
        (
            cx + radius * math.cos(2.0 * math.pi * i / _SEGMENTS),
            cy + radius * math.sin(2.0 * math.pi * i / _SEGMENTS),
        )
        for i in range(_SEGMENTS + 1)
    ]


def _draw_text_below(cx, y, text, color):
    import blf
    font_id = 0
    blf.size(font_id, 14)
    text_w, _ = blf.dimensions(font_id, text)
    x = cx - text_w / 2.0
    blf.position(font_id, x + 1, y - 1, 0)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.85)
    blf.draw(font_id, text)
    blf.position(font_id, x, y, 0)
    blf.color(font_id, *color)
    blf.draw(font_id, text)


def _draw_callback():
    if not _active:
        return

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(1.5)

    if _falloff_edge_px < _radius_px - 0.5:
        shader.uniform_float("color", _INNER_COLOR)
        batch_for_shader(
            shader, 'LINE_STRIP', {"pos": _circle_points(_center_2d, _falloff_edge_px)},
        ).draw(shader)

    shader.uniform_float("color", _OUTER_COLOR)
    batch_for_shader(
        shader, 'LINE_STRIP', {"pos": _circle_points(_center_2d, _radius_px)},
    ).draw(shader)

    gpu.state.line_width_set(1.0)
    shader.uniform_float("color", _CENTER_COLOR)
    cx, cy = _center_2d
    batch_for_shader(shader, 'LINES', {"pos": [(cx - 4, cy), (cx + 4, cy), (cx, cy - 4), (cx, cy + 4)]}).draw(shader)

    gpu.state.blend_set('NONE')

    if _label:
        _draw_text_below(cx, cy - _radius_px - 18.0, _label, _TEXT_COLOR)


def show():
    global _draw_handle, _active
    _active = True
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_callback, (), 'WINDOW', 'POST_PIXEL')
    _tag_redraw()


def update(center_2d, radius_px, falloff_edge_px, label):
    """Refresh the cursor's screen-space position/size -- called from
    `brush_ops.py::_dab()` every tick with values already resolved for the
    current projection mode (`brush_logic.world_radius_to_screen_px()` for
    Surface projection, the raw pixel radius for Screen projection)."""
    global _center_2d, _radius_px, _falloff_edge_px, _label
    _center_2d = center_2d
    _radius_px = radius_px
    _falloff_edge_px = falloff_edge_px
    _label = label
    _tag_redraw()


def hide():
    global _draw_handle, _active
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    _active = False
    _tag_redraw()


def cleanup():
    """Defensive unregister-time cleanup -- guards against an F3 script
    reload landing mid-stroke and leaving a dangling draw handle behind."""
    hide()


def _tag_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
