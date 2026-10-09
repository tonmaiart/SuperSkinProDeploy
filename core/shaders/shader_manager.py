
import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader

from ...core_subsystems.hud_registry import HudSlotRegistry



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



_HUD_SLOT_FONT_SIZE = 20

_HUD_SLOT_BOTTOM_MARGIN = 40
_HUD_SLOT_LINE_HEIGHT = 36
_HUD_REPORT_ROW = 2

_HUD_SLOT_ICON_SIZE = 16
_HUD_SLOT_ICON_GAP = 6

_HUD_SLOT_BG_COLOR = (0.03, 0.03, 0.03)
_HUD_SLOT_BG_ALPHA = 0.65
_HUD_SLOT_BG_PAD_X = 8
_HUD_SLOT_BG_PAD_Y = 6
_HUD_SLOT_BG_GLOW_STEPS = 5
_HUD_SLOT_BG_GLOW_SPREAD = 6
_HUD_SLOT_BG_GLOW_ALPHA = 0.10

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
    for step in range(_HUD_SLOT_BG_GLOW_STEPS, 0, -1):
        spread = _HUD_SLOT_BG_GLOW_SPREAD * step / _HUD_SLOT_BG_GLOW_STEPS
        alpha = _HUD_SLOT_BG_GLOW_ALPHA * (1.0 - (step - 1) / _HUD_SLOT_BG_GLOW_STEPS) * alpha_mult
        _draw_hud_slot_rect(x0 - spread, y0 - spread, x1 + spread, y1 + spread, (*_HUD_SLOT_BG_COLOR, alpha))
    _draw_hud_slot_rect(x0, y0, x1, y1, (*_HUD_SLOT_BG_COLOR, _HUD_SLOT_BG_ALPHA * alpha_mult))


def _draw_hud_slot_icon(x0, y0, size, texture, color):
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
    center_x = context.region.width // 2

    for entry in entries:
        text = entry["text"]
        color = entry["color"]
        icon_texture = entry.get("icon_texture")
        text_w, text_h = blf.dimensions(font_id, text)
        cy = _HUD_SLOT_BOTTOM_MARGIN + entry["slot"] * _HUD_SLOT_LINE_HEIGHT

        icon_advance = (_HUD_SLOT_ICON_SIZE + _HUD_SLOT_ICON_GAP) if icon_texture is not None else 0
        cx = center_x - int(icon_advance + text_w) // 2
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
    cy = _HUD_SLOT_BOTTOM_MARGIN + _HUD_REPORT_ROW * _HUD_SLOT_LINE_HEIGHT

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
    global _report_timer_running
    if HudSlotRegistry.get_active_report() is None:
        _report_timer_running = False
        return None
    ShaderManager._tag_viewport_redraw()
    return 0.05


_report_draw_handle = None


def _hud_slot_expire(owner_id, token):
    HudSlotRegistry.release_slot(owner_id, token=token)
    ShaderManager._tag_viewport_redraw()


_hud_slot_draw_handle = None



class ShaderManager:
    """Stateless macro-dispatcher for viewport redraws and HUD toasts."""

    _deform_generation: int = 0


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
        _toast_state["text"] = text
        if _toast_state["draw_handle"] is None:
            _toast_state["draw_handle"] = bpy.types.SpaceView3D.draw_handler_add(
                _toast_draw_callback, (), 'WINDOW', 'POST_PIXEL'
            )
        self.__class__._tag_viewport_redraw()
        bpy.app.timers.register(lambda: _toast_clear(text), first_interval=duration)

    def show_report(self, text, *, hold=2.0, fade=1.0, color=(1.0, 0.8, 0.0, 1.0)):
        global _report_timer_running
        HudSlotRegistry.request_report(text, hold=hold, fade=fade, color=color)
        self.__class__._tag_viewport_redraw()
        if not _report_timer_running:
            _report_timer_running = True
            bpy.app.timers.register(_report_tick, first_interval=0.05)

    def request_hud_slot(self, owner_id, text, *, slot, timeout=None,
                          color=(1.0, 1.0, 1.0, 1.0), icon_texture=None):
        token = HudSlotRegistry.request_slot(
            owner_id, text, slot=slot, timeout=timeout, color=color, icon_texture=icon_texture
        )
        self.__class__._tag_viewport_redraw()
        if timeout is not None:
            bpy.app.timers.register(lambda: _hud_slot_expire(owner_id, token), first_interval=timeout)

    def release_hud_slot(self, owner_id):
        HudSlotRegistry.release_slot(owner_id)
        self.__class__._tag_viewport_redraw()

    def clear_all_hud_slots(self):
        HudSlotRegistry.clear_all()
        self.__class__._tag_viewport_redraw()


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
