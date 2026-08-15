"""Shader manager for SuperSkinPro — stateless HUD toast dispatcher and viewport redraw helper.

After the Phase 2 refactor, this module no longer owns any GPU draw handles for
bone weight or mask visualization.  Those responsibilities have been moved to:
  - Blender's native Vertex Group Weight Overlay (Single-Bone and Mask modes)
  - features/multi_color_preview/ (MULTI rainbow mode)

What remains here:
  - HUD toast notifications (show_toast)
  - Shared bottom-left HUD stack (request_hud_slot / release_hud_slot /
    clear_all_hud_slots) -- the bpy-facing half of
    core_subsystems/hud_registry's HudSlotRegistry. Any feature domain that
    wants a persistent status label (Multi Color Mode, Mask mode, Bone
    Picker, ...) requests a slot here instead of installing its own draw
    handler for that screen position, so two domains can never draw
    overlapping labels there -- each active request gets its own stacked
    line instead.
  - Dedicated bottom-center report line (show_report) -- the bpy-facing
    half of HudSlotRegistry's request_report/get_active_report. A single
    transient, auto-fading notification line for one-off warnings/errors
    (e.g. layer_crud.py's mask-gap check) that don't belong to any one
    feature domain's persistent status stack.
  - invalidate_color_only / invalidate_and_redraw  (tag_redraw wrappers)
  - bump_deform_generation / get_deform_generation (staleness counter for feature draw callbacks)
  - force_viewport_redraw
"""

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader

from ...core_subsystems.hud_registry import HudSlotRegistry


# ═══════════════════════════════════════════════════════════════════════════
#  Transient HUD Toast
# ═══════════════════════════════════════════════════════════════════════════

_toast_state = {"text": None, "draw_handle": None}


def _toast_draw_callback():
    text = _toast_state["text"]
    if not text:
        return
    context = bpy.context
    if not context.space_data or context.space_data.type != 'VIEW_3D':
        return

    font_id = 0
    blf.size(font_id, 20)
    region_width = context.region.width
    text_w, _ = blf.dimensions(font_id, text)
    cx = max(0, region_width // 2 - int(text_w) // 2)
    cy = context.region.height - 60

    blf.position(font_id, cx + 2, cy - 2, 0)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.85)
    blf.draw(font_id, text)
    blf.position(font_id, cx, cy, 0)
    blf.color(font_id, 1.0, 0.3, 0.3, 1.0)
    blf.draw(font_id, text)


def _toast_clear(expected_text):
    if _toast_state["text"] != expected_text:
        return
    _toast_state["text"] = None
    if _toast_state["draw_handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_toast_state["draw_handle"], 'WINDOW')
        _toast_state["draw_handle"] = None
    ShaderManager._tag_viewport_redraw()


# ═══════════════════════════════════════════════════════════════════════════
#  Shared Bottom-Left HUD Stack
#
#  Unlike the toast above, this draw handler is installed once at addon
#  register() and left in place for the addon's lifetime (see register()/
#  unregister() below) -- it must be able to react to any feature domain's
#  request_hud_slot() call at any time, not just while a single toast is
#  live. It draws nothing whenever no feature currently holds a slot, so the
#  idle cost is a single dict-emptiness check per redraw.
#
#  Every active (non-expired) slot gets its own stacked line -- requesting a
#  slot never hides another domain's already-active one. Anchored bottom-left
#  rather than bottom-center; the left margin intentionally matches
#  interface/utils/shortcut_overlay.py's _LEFT_MARGIN (90px, clears
#  Blender's native Toolbar) so both HUDs line up on the same left edge.
#  Not imported from there directly -- core/ must not depend on interface/.
# ═══════════════════════════════════════════════════════════════════════════

_HUD_SLOT_FONT_SIZE = 20      # 24 * 0.6 -- 40% smaller than the original HUD

_HUD_SLOT_LEFT_MARGIN = 90    # matches shortcut_overlay.py's _LEFT_MARGIN
_HUD_SLOT_BOTTOM_MARGIN = 40  # first (anchor) line's y from the bottom edge
_HUD_SLOT_LINE_HEIGHT = 36    # 50 * 0.6 -- vertical distance between stacked
                               # lines, scaled down with everything else so
                               # adjacent owners' (now smaller) backdrop
                               # boxes still clear each other with a gap

_HUD_SLOT_ICON_SIZE = 16      # 26 * 0.6, square icon side, in pixels
_HUD_SLOT_ICON_GAP = 6        # 10 * 0.6, gap between icon and text

# Backdrop panel style matches interface/utils/shortcut_overlay.py's
# _draw_backdrop() (same color/alpha/glow constants), scaled 40% smaller on
# pad/spread to match the smaller text -- reads as one consistent HUD
# "look" across both draw handlers, just at this stack's own smaller size.
_HUD_SLOT_BG_COLOR = (0.03, 0.03, 0.03)
_HUD_SLOT_BG_ALPHA = 0.65
_HUD_SLOT_BG_PAD_X = 8         # 14 * 0.6
_HUD_SLOT_BG_PAD_Y = 6         # 10 * 0.6
_HUD_SLOT_BG_GLOW_STEPS = 5
_HUD_SLOT_BG_GLOW_SPREAD = 6   # 10 * 0.6
_HUD_SLOT_BG_GLOW_ALPHA = 0.10

# Single-pixel outline ring -- the original ±1/±2 12-offset ring (sized for
# the larger 24pt text) reads as an oversized blob at this smaller font.
_HUD_SLOT_STROKE_COLOR = (0.0, 0.0, 0.0, 0.95)
_HUD_SLOT_STROKE_OFFSETS = (
    (-1, -1), (0, -1), (1, -1),
    (-1,  0),          (1,  0),
    (-1,  1), (0,  1), (1,  1),
)


def _draw_hud_slot_rect(x0, y0, x1, y1, color):
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, 'TRI_FAN', {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]}).draw(shader)


def _draw_hud_slot_backdrop(x0, y0, x1, y1, alpha_mult=1.0):
    """Same glow-style backdrop as shortcut_overlay.py's _draw_backdrop():
    a solid core panel plus a few widening, fading rects behind it, faking a
    soft blur falloff (blf's POST_PIXEL handler has no sampled/blurred copy
    of the framebuffer to draw a real gaussian blur from).

    alpha_mult scales every alpha in this backdrop uniformly -- used by the
    fading report line (_report_draw_callback) to fade its backdrop in step
    with its text; the always-opaque owner stack (_hud_slot_draw_callback)
    just leaves it at the default 1.0."""
    for step in range(_HUD_SLOT_BG_GLOW_STEPS, 0, -1):
        spread = _HUD_SLOT_BG_GLOW_SPREAD * step / _HUD_SLOT_BG_GLOW_STEPS
        alpha = _HUD_SLOT_BG_GLOW_ALPHA * (1.0 - (step - 1) / _HUD_SLOT_BG_GLOW_STEPS) * alpha_mult
        _draw_hud_slot_rect(x0 - spread, y0 - spread, x1 + spread, y1 + spread, (*_HUD_SLOT_BG_COLOR, alpha))
    _draw_hud_slot_rect(x0, y0, x1, y1, (*_HUD_SLOT_BG_COLOR, _HUD_SLOT_BG_ALPHA * alpha_mult))


def _draw_hud_slot_icon(x0, y0, size, texture, color):
    """Draw *texture* as a *size*x*size* square at (x0, y0), tinted by
    *color*. Uses the 'IMAGE_COLOR' built-in shader (samples "image",
    multiplies by uniform "color") -- the icon source PNGs are white-on-
    transparent, so this is what lets one texture serve every HUD color."""
    shader = gpu.shader.from_builtin('IMAGE_COLOR')
    batch = batch_for_shader(
        shader, 'TRI_FAN',
        {
            "pos": [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)],
            "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)],
        },
    )
    shader.bind()
    shader.uniform_sampler("image", texture)
    shader.uniform_float("color", color)
    batch.draw(shader)


def _hud_slot_draw_callback():
    entries = HudSlotRegistry.get_active_entries()
    if not entries:
        return
    context = bpy.context
    if not context.space_data or context.space_data.type != 'VIEW_3D':
        return

    font_id = 0
    blf.size(font_id, _HUD_SLOT_FONT_SIZE)
    cx = _HUD_SLOT_LEFT_MARGIN

    for entry in entries:
        text = entry["text"]
        color = entry["color"]
        icon_texture = entry.get("icon_texture")
        text_w, text_h = blf.dimensions(font_id, text)
        # Fixed reserved row, not draw order -- an inactive slot leaves a
        # gap instead of the lines above it sliding down to fill it (see
        # core_subsystems/hud_registry/hud_slot_registry.py's docstring).
        cy = _HUD_SLOT_BOTTOM_MARGIN + entry["slot"] * _HUD_SLOT_LINE_HEIGHT

        icon_advance = (_HUD_SLOT_ICON_SIZE + _HUD_SLOT_ICON_GAP) if icon_texture is not None else 0
        text_x = cx + icon_advance

        x0 = cx - _HUD_SLOT_BG_PAD_X
        y0 = cy - _HUD_SLOT_BG_PAD_Y
        x1 = text_x + text_w + _HUD_SLOT_BG_PAD_X
        y1 = cy + text_h + _HUD_SLOT_BG_PAD_Y

        gpu.state.blend_set('ALPHA')
        _draw_hud_slot_backdrop(x0, y0, x1, y1)

        if icon_texture is not None:
            icon_y = cy + (text_h - _HUD_SLOT_ICON_SIZE) / 2.0
            _draw_hud_slot_icon(cx, icon_y, _HUD_SLOT_ICON_SIZE, icon_texture, color)

        gpu.state.blend_set('NONE')

        blf.color(font_id, *_HUD_SLOT_STROKE_COLOR)
        for dx, dy in _HUD_SLOT_STROKE_OFFSETS:
            blf.position(font_id, text_x + dx, cy + dy, 0)
            blf.draw(font_id, text)

        blf.position(font_id, text_x, cy, 0)
        blf.color(font_id, *color)
        blf.draw(font_id, text)


# ═══════════════════════════════════════════════════════════════════════════
#  Dedicated Bottom-Center Report Line
#
#  A single transient, auto-fading notification -- distinct from the
#  per-owner stack above (which is bottom-left, one persistent row per
#  feature domain, held until released/timed-out with no fade). This is for
#  one-off warnings/errors that don't belong to any feature domain's status
#  row, e.g. layer_crud.py's mask-gap check. Same visual language (font
#  size, backdrop, stroke) as the owner stack for a consistent HUD "look".
# ═══════════════════════════════════════════════════════════════════════════

_report_timer_running = False


def _report_draw_callback():
    entry = HudSlotRegistry.get_active_report()
    if entry is None:
        return
    context = bpy.context
    if not context.space_data or context.space_data.type != 'VIEW_3D':
        return

    font_id = 0
    blf.size(font_id, _HUD_SLOT_FONT_SIZE)
    text = entry["text"]
    alpha = entry["alpha"]
    color = entry["color"]
    text_w, text_h = blf.dimensions(font_id, text)

    cx = context.region.width // 2 - int(text_w) // 2
    cy = _HUD_SLOT_BOTTOM_MARGIN

    x0 = cx - _HUD_SLOT_BG_PAD_X
    y0 = cy - _HUD_SLOT_BG_PAD_Y
    x1 = cx + text_w + _HUD_SLOT_BG_PAD_X
    y1 = cy + text_h + _HUD_SLOT_BG_PAD_Y

    gpu.state.blend_set('ALPHA')
    _draw_hud_slot_backdrop(x0, y0, x1, y1, alpha_mult=alpha)
    gpu.state.blend_set('NONE')

    stroke_r, stroke_g, stroke_b, stroke_a = _HUD_SLOT_STROKE_COLOR
    blf.color(font_id, stroke_r, stroke_g, stroke_b, stroke_a * alpha)
    for dx, dy in _HUD_SLOT_STROKE_OFFSETS:
        blf.position(font_id, cx + dx, cy + dy, 0)
        blf.draw(font_id, text)

    blf.position(font_id, cx, cy, 0)
    blf.color(font_id, color[0], color[1], color[2], color[3] * alpha)
    blf.draw(font_id, text)


def _report_tick():
    """Repeating bpy.app.timers callback -- keeps tagging the viewport for
    redraw every tick so the fade animates smoothly even if nothing else
    would otherwise trigger a redraw (e.g. the user's mouse is idle).
    Stops itself (returns None) once the report has fully expired."""
    global _report_timer_running
    if HudSlotRegistry.get_active_report() is None:
        _report_timer_running = False
        return None
    ShaderManager._tag_viewport_redraw()
    return 0.05


_report_draw_handle = None


def _hud_slot_expire(owner_id, token):
    """bpy.app.timers callback for a timeout= request. Only releases if this
    owner's slot hasn't since been refreshed by a newer request (see
    HudSlotRegistry.release_slot's token guard)."""
    HudSlotRegistry.release_slot(owner_id, token=token)
    ShaderManager._tag_viewport_redraw()


_hud_slot_draw_handle = None


# ═══════════════════════════════════════════════════════════════════════════
#  ShaderManager
# ═══════════════════════════════════════════════════════════════════════════

class ShaderManager:
    """Stateless macro-dispatcher for viewport redraws and HUD toasts."""

    # Deform-generation counter — formerly in visualizer_base.py.
    # Incremented by pipeline.finish() so GPU draw callbacks can detect
    # weight changes at the same Blender frame without importing from core.
    _deform_generation: int = 0

    # ── Public API ──

    @classmethod
    def bump_deform_generation(cls) -> int:
        cls._deform_generation += 1
        return cls._deform_generation

    @classmethod
    def get_deform_generation(cls) -> int:
        return cls._deform_generation

    @classmethod
    def invalidate_color_only(cls):
        cls._tag_viewport_redraw()

    def invalidate_and_redraw(self):
        self.__class__._tag_redraw_all_areas()

    @staticmethod
    def force_viewport_redraw():
        ShaderManager._tag_viewport_redraw()

    def show_toast(self, text, duration=1.0):
        """Display a short-lived auto-dismissing HUD notification."""
        _toast_state["text"] = text
        if _toast_state["draw_handle"] is None:
            _toast_state["draw_handle"] = bpy.types.SpaceView3D.draw_handler_add(
                _toast_draw_callback, (), 'WINDOW', 'POST_PIXEL'
            )
        self.__class__._tag_viewport_redraw()
        bpy.app.timers.register(lambda: _toast_clear(text), first_interval=duration)

    def show_report(self, text, *, hold=2.0, fade=1.0, color=(1.0, 0.8, 0.0, 1.0)):
        """Display a transient, auto-fading report line bottom-center of the
        viewport, replacing whatever report is currently showing.

        Unlike show_toast (which just vanishes after its duration) this
        eases from fully opaque to invisible over *fade* seconds, and unlike
        request_hud_slot (a persistent per-owner row until released) there
        is only ever one report and it always tears itself down.

        Args:
            text: The message to display.
            hold: Seconds the line stays fully opaque before fading starts.
            fade: Seconds the line takes to fade out after the hold window.
            color: RGBA color for the text (and its backdrop's fade-in-step
                alpha). Defaults to the same yellow already used by the
                bottom-left owner stack's WARNING-style lines.
        """
        global _report_timer_running
        HudSlotRegistry.request_report(text, hold=hold, fade=fade, color=color)
        self.__class__._tag_viewport_redraw()
        if not _report_timer_running:
            _report_timer_running = True
            bpy.app.timers.register(_report_tick, first_interval=0.05)

    def request_hud_slot(self, owner_id, text, *, slot, timeout=None,
                          color=(1.0, 1.0, 1.0, 1.0), icon_texture=None):
        """Request a line on the shared bottom-left HUD stack for *owner_id*.

        Every active (non-expired) request draws on its own fixed row --
        requesting a slot never hides another domain's already-active one,
        and an inactive slot leaves a gap rather than the rows above it
        sliding down to fill it. Re-requesting with the same owner_id (e.g.
        to update the text) keeps this owner's existing row.

        Args:
            owner_id: Stable identifier for the calling feature domain
                (e.g. "overlay_color"). Also used to release the slot later.
            text: The label to display on this owner's line.
            slot: Fixed row number, ``0`` at the anchor (bottom-most),
                increasing upward. A static reservation, not a priority --
                see the canonical slot table in this docstring's "Shared HUD
                Stack" counterpart, core/facade/README.md.
            timeout: Optional seconds after which this request auto-releases
                itself, for transient status text that shouldn't require an
                explicit release call. ``None`` (default) means the slot is
                held until release_hud_slot(owner_id) is called explicitly.
            color: RGBA color for this owner's text (and its icon, if any).
            icon_texture: Optional pre-built ``gpu.types.GPUTexture`` drawn
                to the left of the text, tinted by *color* -- see
                ``interface/utils/icons.py``'s ``get_*_icon_texture()``
                helpers for how to build one. ``None`` (default) draws no
                icon. core/ never imports interface/, so this module only
                stores and draws whatever texture object the caller built.
        """
        token = HudSlotRegistry.request_slot(
            owner_id, text, slot=slot, timeout=timeout, color=color, icon_texture=icon_texture
        )
        self.__class__._tag_viewport_redraw()
        if timeout is not None:
            bpy.app.timers.register(lambda: _hud_slot_expire(owner_id, token), first_interval=timeout)

    def release_hud_slot(self, owner_id):
        """Release *owner_id*'s line from the shared bottom-left HUD stack, if it currently holds one."""
        HudSlotRegistry.release_slot(owner_id)
        self.__class__._tag_viewport_redraw()

    def clear_all_hud_slots(self):
        """Discard every line on the shared HUD stack, regardless of owner.

        Used on Edit Layer Weight exit (all current HUD consumers only make
        sense inside that mode) so no stale line can linger into Object Mode.
        """
        HudSlotRegistry.clear_all()
        self.__class__._tag_viewport_redraw()

    # ── Internal helpers ──

    @staticmethod
    def _tag_viewport_redraw():
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

    @staticmethod
    def _tag_redraw_all_areas():
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in {'VIEW_3D', 'PROPERTIES', 'UI'}:
                    area.tag_redraw()


def register():
    global _hud_slot_draw_handle, _report_draw_handle
    if _hud_slot_draw_handle is None:
        _hud_slot_draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _hud_slot_draw_callback, (), 'WINDOW', 'POST_PIXEL'
        )
    if _report_draw_handle is None:
        _report_draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _report_draw_callback, (), 'WINDOW', 'POST_PIXEL'
        )


def unregister():
    global _hud_slot_draw_handle, _report_draw_handle, _report_timer_running
    if _toast_state["draw_handle"] is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_toast_state["draw_handle"], 'WINDOW')
        except Exception:
            pass
        _toast_state["draw_handle"] = None
    _toast_state["text"] = None
    if _hud_slot_draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_hud_slot_draw_handle, 'WINDOW')
        except Exception:
            pass
        _hud_slot_draw_handle = None
    if _report_draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_report_draw_handle, 'WINDOW')
        except Exception:
            pass
        _report_draw_handle = None
    _report_timer_running = False
    HudSlotRegistry.clear_report()
    HudSlotRegistry.clear_all()
    ShaderManager._deform_generation = 0
