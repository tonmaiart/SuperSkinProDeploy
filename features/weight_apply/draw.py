"""Weight Apply gesture HUD -- horizontal drag-axis line + operation/value
readout, drawn only while SUPERSKIN_OT_weight_gesture's modal Alt-drag is
active (ops.py calls show()/update()/hide() around its own invoke/modal/
release lifecycle -- there is no persistent draw handler here, unlike
bone_picker/deform_overlay.py's always-on overlay).

Anchored to the bottom-center of the viewport region (recomputed from
`context.region` every draw, so it stays centered through a resize) rather
than tracking the cursor -- same bottom-center placement convention as
bone_picker/deform_overlay.py's "BONE PICKER" HUD and
overlay_color/multi_color_draw.py's "Multi Color" HUD.

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
_fine_mode = False   # True while Ctrl is held (fine sub-grid mode) -- see ops.py's _FINE_MODE_DIVISOR
                     # -- only affects the text suffix below, never the axis geometry

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

# Grid "stop" tick marks along the axis, one per step, so the drag reads as
# discrete stops rather than a bare continuous line -- `_drag_value` itself
# is now the already-quantized value ops.py applies (see update()'s
# docstring), so the marker/fill-bar above naturally jump stop-to-stop too,
# with no extra snapping logic needed here. Kept as a separate constant (not
# imported from ops.py) to avoid a circular import -- ops.py already imports
# this module -- so keep this in sync with ops.py's own `_GRID_COARSE_STEP`
# if it changes. Fine mode (Ctrl held) uses a finer step
# (`ops.py`'s `_GRID_FINE_STEP`) but draws identically -- see `_fine_mode`'s
# comment above and `_draw_callback()`'s text-suffix-only handling below.
_GRID_COARSE_STEP = 0.01
_STOP_TICK_HEIGHT = 5.0
_STOP_TICK_COLOR = (1.0, 1.0, 1.0, 0.28)
# Caps how many stop ticks are actually DRAWN, independent of the real
# _GRID_COARSE_STEP snap precision -- at a 0.01 step the true grid has up to
# 200 buckets (smooth_sharpen's 2.0 visual range), which would blur into a
# solid smear on a ~300px line. Every tick actually drawn still sits on a
# real, exactly-snappable value (a sparser sample, at a stride computed
# below), so this only thins the DISPLAY, never the precision.
_MAX_STOP_TICKS_DRAWN = 40

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
    # Ctrl (fine mode) never changes the axis geometry -- same window, same
    # ticks, same everything below -- it only switches ops.py's underlying
    # snap step to a finer grid and appends "(Slow)" to the text at the
    # bottom of this function. See `_fine_mode`'s comment above.
    window_center, window_half = 0.0, visual_range

    def _value_ratio(value):
        return (value - window_center) / window_half if window_half else 0.0

    raw_ratio = _value_ratio(_drag_value)
    ratio = max(-1.0, min(1.0, raw_ratio))
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

    # Zero tick (always within the window -- the window is always the full
    # [-visual_range, visual_range] span regardless of fine mode) + end
    # ticks at the window's own boundaries.
    shader.uniform_float("color", _TICK_COLOR)
    tick_pos = [
        (cx - _LINE_HALF_LENGTH, cy - _END_TICK_HEIGHT / 2), (cx - _LINE_HALF_LENGTH, cy + _END_TICK_HEIGHT / 2),
        (cx + _LINE_HALF_LENGTH, cy - _END_TICK_HEIGHT / 2), (cx + _LINE_HALF_LENGTH, cy + _END_TICK_HEIGHT / 2),
    ]
    zero_ratio = _value_ratio(0.0)
    if -1.0 <= zero_ratio <= 1.0:
        zero_x = cx + zero_ratio * _LINE_HALF_LENGTH
        tick_pos += [(zero_x, cy - _TICK_HEIGHT), (zero_x, cy + _TICK_HEIGHT)]
    batch_for_shader(shader, 'LINES', {"pos": tick_pos}).draw(shader)

    # Coarse grid stop ticks -- one small mark per _GRID_COARSE_STEP within
    # the visible window. Drawn identically regardless of fine mode -- Ctrl
    # never changes this geometry, only the underlying snap step in ops.py.
    n_coarse = round(visual_range / _GRID_COARSE_STEP)
    stride = max(1, round(n_coarse / max(1, _MAX_STOP_TICKS_DRAWN // 2)))
    stop_pos = []
    for step in range(-n_coarse, n_coarse + 1, stride):
        if step == 0 or abs(step) == n_coarse:
            continue
        value = step * _GRID_COARSE_STEP
        x = cx + _value_ratio(value) * _LINE_HALF_LENGTH
        stop_pos.append((x, cy - _STOP_TICK_HEIGHT))
        stop_pos.append((x, cy + _STOP_TICK_HEIGHT))
    if stop_pos:
        shader.uniform_float("color", _STOP_TICK_COLOR)
        batch_for_shader(shader, 'LINES', {"pos": stop_pos}).draw(shader)

    # Fill bar from the window's own center (always zero, mapping to `cx`
    # exactly, regardless of fine mode) to the current value
    gpu.state.line_width_set(4.0)
    shader.uniform_float("color", _MARKER_COLOR)
    batch_for_shader(shader, 'LINES', {"pos": [(cx, cy), (marker_x, cy)]}).draw(shader)

    # Marker at the current position
    gpu.state.line_width_set(1.0)
    marker_half = _TICK_HEIGHT * 0.9
    batch_for_shader(shader, 'LINES', {"pos": [
        (marker_x, cy - marker_half), (marker_x, cy + marker_half),
    ]}).draw(shader)

    # Overflow chevron -- the real (unclamped) value has run past
    # visual_range (smooth_sharpen only; add_scale is hard-clamped so this
    # never fires for it).
    if abs(raw_ratio) > 1.0:
        chevron_dir = 1.0 if raw_ratio > 0 else -1.0
        tip_x = marker_x + chevron_dir * 6.0
        batch_for_shader(shader, 'LINES', {"pos": [
            (marker_x, cy - marker_half), (tip_x, cy),
            (tip_x, cy), (marker_x, cy + marker_half),
        ]}).draw(shader)

    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')

    # Ctrl (fine mode) appends "(Slow)" to the readout -- no separate
    # visual state beyond this suffix (see the window/tick comments above).
    mode_suffix = "  (Slow)" if _fine_mode else ""
    label = _GESTURE_LABELS.get(_real_action, _real_action or "Weight Apply")
    _draw_text_centered(cx, cy + _TEXT_MARGIN, f"{label}: {_intensity:.2f}{mode_suffix}", _TEXT_COLOR)


def show(pair_action):
    """Install the draw handler and pin the gesture's fixed pair -- called
    once from ops.py's invoke(), before any drag has actually started
    (values default to the neutral 0.0 state until the first update()
    call)."""
    global _draw_handle, _active, _pair_action, _real_action, _intensity, _drag_value
    global _fine_mode
    _active = True
    _pair_action = pair_action
    _real_action = ""
    _intensity = 0.0
    _drag_value = 0.0
    _fine_mode = False
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_callback, (), 'WINDOW', 'POST_PIXEL')
    _tag_redraw()


def update(real_action, intensity, drag_value, fine_mode=False):
    """Refresh the live readout -- called from ops.py's modal() on every
    MOUSEMOVE (cheap: pure Python + tag_redraw, no FFI call) so the marker
    and text track the drag in real time, independent of the throttled
    TIMER tick that actually applies the weight change.

    `drag_value` is ops.py's already-QUANTIZED grid value (`current_idx /
    _grid_n(fine_mode)`, matching exactly what `modal()`'s TIMER branch
    applies), not the raw continuous drag position -- this is what makes the
    marker/fill-bar in `_draw_callback` jump stop-to-stop instead of
    tracking the mouse smoothly, matching the stop tick marks drawn along
    the axis.

    `fine_mode` reflects ops.py's Ctrl-held fine sub-grid state (default arg
    keeps this call signature-compatible with any other caller) -- affects
    only the "(Slow)" text suffix, never the axis geometry -- see
    `_draw_callback`'s suffix composition above."""
    global _real_action, _intensity, _drag_value, _fine_mode
    _real_action = real_action
    _intensity = intensity
    _drag_value = drag_value
    _fine_mode = fine_mode
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
    _unregister_affected_only_sync()


# ── "Smooth Affected Only" HUD sync ─────────────────────────────────────────
#
# Owns the color/slot for this HUD line, and is the only place that ever
# touches the shared HUD stack for it -- weight_apply_feature.py's
# smooth_affected_only update= callback just calls sync_affected_only_hud()
# below on every manual toggle rather than duplicating these constants.
#
# That alone would still go stale across an Edit Layer Weight exit/
# re-entry, though: CoreFacade.clear_all_hud_slots() sweeps every owner's
# row on every exit path (see docs/core-interfaces/facade-api.md's "Shared
# HUD Stack" section), and nothing re-fires the property's update= callback
# just from re-entering Edit Mode with the checkbox already left on from a
# previous session. The same gap deform_bone_viewer/draw.py's Mask/Bone HUD
# line already solved, and the same fix: a persistent, always-installed
# draw handler that also calls sync_affected_only_hud() on every redraw,
# so it stays correct regardless of which code path (or which prior
# session) left the checkbox on.
_sync_draw_handle = None
_last_affected_only_state = None  # None (no line) | True (line requested)

_AFFECTED_ONLY_OWNER_ID = "weight_apply"
_AFFECTED_ONLY_LABEL = "Smooth Affected Only"
_AFFECTED_ONLY_COLOR = (1.0, 0.3, 0.6, 1.0)  # pink
_AFFECTED_ONLY_SLOT = 3  # see docs/core-interfaces/facade-api.md's reserved-slot table


def _affected_only_active(obj):
    if not obj or obj.type != 'MESH' or obj.mode != 'EDIT':
        return False
    try:
        wm = bpy.context.window_manager
        return bool(wm.superskin_weight_apply_prefs.smooth_affected_only)
    except Exception:
        return False


def sync_affected_only_hud():
    """Re-evaluate whether the pink 'Smooth Affected Only' HUD line should
    be showing right now, and request/release it on the shared stack only
    if that differs from what's already there. Called both from the
    persistent draw handler below (every redraw, so re-entering Edit Mode
    always rechecks) and directly from weight_apply_feature.py's
    smooth_affected_only update= callback (so a manual toggle reflects
    immediately instead of waiting for the next viewport redraw)."""
    global _last_affected_only_state
    state = _affected_only_active(bpy.context.active_object)
    if state == _last_affected_only_state:
        return
    from ...core.facade import CoreFacade
    if state:
        CoreFacade.request_hud_slot(
            _AFFECTED_ONLY_OWNER_ID, _AFFECTED_ONLY_LABEL,
            slot=_AFFECTED_ONLY_SLOT, color=_AFFECTED_ONLY_COLOR,
        )
    else:
        CoreFacade.release_hud_slot(_AFFECTED_ONLY_OWNER_ID)
    _last_affected_only_state = state


def _register_affected_only_sync():
    global _sync_draw_handle
    if _sync_draw_handle is not None:
        return
    _sync_draw_handle = bpy.types.SpaceView3D.draw_handler_add(
        sync_affected_only_hud, (), 'WINDOW', 'POST_PIXEL'
    )


def _unregister_affected_only_sync():
    global _sync_draw_handle, _last_affected_only_state
    if _sync_draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_sync_draw_handle, 'WINDOW')
        _sync_draw_handle = None
    if _last_affected_only_state:
        from ...core.facade import CoreFacade
        CoreFacade.release_hud_slot(_AFFECTED_ONLY_OWNER_ID)
    _last_affected_only_state = None


def register():
    """Installs the persistent 'Smooth Affected Only' HUD-sync handler --
    called once from __init__.py's register(), left installed for the
    addon's lifetime (same convention as deform_bone_viewer/draw.py's own
    persistent handler). Does not touch the gesture HUD above, which is
    still only shown/hidden around a live modal drag."""
    _register_affected_only_sync()


def _tag_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
