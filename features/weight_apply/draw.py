"""Weight Apply gesture HUD -- horizontal drag-axis line + operation/value
readout, drawn only while SUPERSKIN_OT_weight_gesture's modal Alt-drag is
active (ops.py calls show()/update()/hide() around its own invoke/modal/
release lifecycle -- there is no persistent draw handler here, unlike
bone_picker/deform_overlay.py's always-on overlay).

Anchored to the bottom-center of the viewport region (recomputed from
`context.region` every draw, so it stays centered through a resize) rather
than tracking the cursor -- same bottom-center placement convention as
bone_picker/deform_overlay.py's "BONE PICKER" HUD and
overlay_color/multi_color_draw.py's "MULTI COLOR MODE" HUD.

Same GPU draw conventions as those: UNIFORM_COLOR shader, batch_for_shader,
POST_PIXEL on SpaceView3D. Everything is drawn in plain white (varying only
in opacity/line-width for hierarchy) rather than color-coded per action --
sizes are hardcoded, no user-facing configuration.
"""

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader

_draw_handle = None

_active = False             # True while the gesture's modal is running, False otherwise
_pair_action = "add_scale"  # "add_scale" or "smooth_sharpen" -- selects the visual range below
_real_action = ""           # "add"/"scale"/"smooth"/"sharpen" -- selects the label
_intensity = 0.0
_drag_value = 0.0
_slow_tier = 0

_LINE_HALF_LENGTH = 150.0  # pixels each side of center -- matches ops.py's 300px-per-unit drag sensitivity at normal speed
_TICK_HEIGHT = 8.0
_END_TICK_HEIGHT = 12.0
_LINE_Y_OFFSET = 60.0   # pixels above the bottom of the region
_TEXT_MARGIN = 22.0     # pixels above the line

# How many `drag_value` units the line's full half-length represents.
# add_scale is hard-clamped to [-1, 1] in ops.py already, so 1.0 fills the
# line exactly. smooth_sharpen is deliberately unclamped -- 2.0 keeps the
# common single-gesture range legible before the marker pegs at the end
# with the overflow chevron below.
_VISUAL_RANGE = {
    "add_scale": 1.0,
    "smooth_sharpen": 2.0,
}

_LINE_COLOR = (1.0, 1.0, 1.0, 0.35)
_TICK_COLOR = (1.0, 1.0, 1.0, 0.6)
_MARKER_COLOR = (1.0, 1.0, 1.0, 1.0)
_TEXT_COLOR = (1.0, 1.0, 1.0, 1.0)

_GESTURE_LABELS = {
    "add": "Add Weight",
    "scale": "Scale Weight",
    "smooth": "Smooth Weight",
    "sharpen": "Sharpen Weight",
}


def _draw_text_centered(cx, y, text, color):
    font_id = 0
    blf.size(font_id, 16)
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
    region = bpy.context.region
    if not region:
        return
    cx, cy = region.width / 2.0, _LINE_Y_OFFSET

    visual_range = _VISUAL_RANGE.get(_pair_action, 1.0)
    ratio = max(-1.0, min(1.0, _drag_value / visual_range)) if visual_range else 0.0
    marker_x = cx + ratio * _LINE_HALF_LENGTH

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()
    gpu.state.blend_set('ALPHA')

    # Baseline (full drag-axis extent)
    gpu.state.line_width_set(2.0)
    shader.uniform_float("color", _LINE_COLOR)
    batch_for_shader(shader, 'LINES', {"pos": [
        (cx - _LINE_HALF_LENGTH, cy), (cx + _LINE_HALF_LENGTH, cy),
    ]}).draw(shader)

    # Zero tick + end ticks
    shader.uniform_float("color", _TICK_COLOR)
    batch_for_shader(shader, 'LINES', {"pos": [
        (cx, cy - _TICK_HEIGHT), (cx, cy + _TICK_HEIGHT),
        (cx - _LINE_HALF_LENGTH, cy - _END_TICK_HEIGHT / 2), (cx - _LINE_HALF_LENGTH, cy + _END_TICK_HEIGHT / 2),
        (cx + _LINE_HALF_LENGTH, cy - _END_TICK_HEIGHT / 2), (cx + _LINE_HALF_LENGTH, cy + _END_TICK_HEIGHT / 2),
    ]}).draw(shader)

    # Fill bar from zero to the current (clamped-for-display) value
    gpu.state.line_width_set(4.0)
    shader.uniform_float("color", _MARKER_COLOR)
    batch_for_shader(shader, 'LINES', {"pos": [(cx, cy), (marker_x, cy)]}).draw(shader)

    # Marker at the current position
    gpu.state.line_width_set(1.0)
    marker_half = _TICK_HEIGHT * 0.9
    batch_for_shader(shader, 'LINES', {"pos": [
        (marker_x, cy - marker_half), (marker_x, cy + marker_half),
    ]}).draw(shader)

    # Overflow chevron -- the real (unclamped) value has run past what the
    # line can show (smooth_sharpen only; add_scale is hard-clamped so this
    # never fires for it).
    if abs(_drag_value) > visual_range:
        chevron_dir = 1.0 if _drag_value > 0 else -1.0
        tip_x = marker_x + chevron_dir * 6.0
        batch_for_shader(shader, 'LINES', {"pos": [
            (marker_x, cy - marker_half), (tip_x, cy),
            (tip_x, cy), (marker_x, cy + marker_half),
        ]}).draw(shader)

    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')

    slow_suffix = f"  [Slow x{_slow_tier}]" if _slow_tier else ""
    label = _GESTURE_LABELS.get(_real_action, _real_action or "Weight Apply")
    _draw_text_centered(cx, cy + _TEXT_MARGIN, f"{label}: {_intensity:.2f}{slow_suffix}", _TEXT_COLOR)


def show(pair_action):
    """Install the draw handler and pin the gesture's fixed pair -- called
    once from ops.py's invoke(), before any drag has actually started
    (values default to the neutral 0.0 state until the first update()
    call)."""
    global _draw_handle, _active, _pair_action, _real_action, _intensity, _drag_value, _slow_tier
    _active = True
    _pair_action = pair_action
    _real_action = ""
    _intensity = 0.0
    _drag_value = 0.0
    _slow_tier = 0
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_callback, (), 'WINDOW', 'POST_PIXEL')
    _tag_redraw()


def update(real_action, intensity, drag_value, slow_tier):
    """Refresh the live readout -- called from ops.py's modal() on every
    MOUSEMOVE (cheap: pure Python + tag_redraw, no FFI call) so the marker
    and text track the drag in real time, independent of the throttled
    TIMER tick that actually applies the weight change."""
    global _real_action, _intensity, _drag_value, _slow_tier
    _real_action = real_action
    _intensity = intensity
    _drag_value = drag_value
    _slow_tier = slow_tier
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
    reload landing mid-gesture and leaving a dangling draw handle behind."""
    hide()


def _tag_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
