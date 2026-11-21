
import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader

from ...core_subsystems.hud_registry import HudSlotRegistry, hud_layout



_HUD_SLOT_ICON_SIZE = 16
_HUD_SLOT_ICON_GAP = 6

_HUD_SLOT_BG_COLOR = (0.12, 0.12, 0.13, 1.0)
_HUD_SLOT_BG_PAD_X = 10
_HUD_SLOT_BG_PAD_Y = 6
_HUD_SLOT_BORDER_WIDTH = 1.5
_HUD_SLOT_RADIUS_RATIO = 0.2

_badge_shader = None

_BADGE_VERTEX_SOURCE = """
void main()
{
    pixel = pos;
    gl_Position = vec4(pos / viewport.xy * 2.0 - 1.0, 0.0, 1.0);
}
"""

_BADGE_FRAGMENT_SOURCE = """
float rounded_box(vec2 p, vec2 extent, float radius)
{
    vec2 q = abs(p) - extent + radius;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
}

void main()
{
    vec2 extent = rect.zw * 0.5;
    float d = rounded_box(pixel - (rect.xy + extent), extent, params.x);
    float outer = clamp(0.5 - d, 0.0, 1.0);
    float inner = clamp(0.5 - (d + params.y), 0.0, 1.0);
    vec4 color = mix(borderColor, fillColor, inner);
    FragColor = vec4(color.rgb, color.a * outer);
}
"""


def _badge_shader_get():
    global _badge_shader
    if _badge_shader is None:
        iface = gpu.types.GPUStageInterfaceInfo("ssp_hud_badge_iface")
        iface.smooth('VEC2', "pixel")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('VEC4', "viewport")
        info.push_constant('VEC4', "rect")
        info.push_constant('VEC4', "params")
        info.push_constant('VEC4', "fillColor")
        info.push_constant('VEC4', "borderColor")
        info.vertex_in(0, 'VEC2', "pos")
        info.vertex_out(iface)
        info.fragment_out(0, 'VEC4', "FragColor")
        info.vertex_source(_BADGE_VERTEX_SOURCE)
        info.fragment_source(_BADGE_FRAGMENT_SOURCE)
        _badge_shader = gpu.shader.create_from_info(info)
    return _badge_shader


def _to_linear(color, alpha_mult=1.0):
    r, g, b, a = color
    return tuple(
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)
    ) + (a * alpha_mult,)


def _draw_hud_slot_badge(x0, y0, x1, y1, border_color, alpha_mult=1.0):
    shader = _badge_shader_get()
    w, h = x1 - x0, y1 - y0
    quad = ((x0 - 1, y0 - 1), (x1 + 1, y0 - 1), (x1 + 1, y1 + 1), (x0 - 1, y1 + 1))
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": quad})
    _vx, _vy, vw, vh = gpu.state.viewport_get()
    shader.bind()
    shader.uniform_float("viewport", (vw, vh, 0.0, 0.0))
    shader.uniform_float("rect", (x0, y0, w, h))
    shader.uniform_float("params", (h * _HUD_SLOT_RADIUS_RATIO, _HUD_SLOT_BORDER_WIDTH, 0.0, 0.0))
    shader.uniform_float("fillColor", _to_linear(_HUD_SLOT_BG_COLOR, alpha_mult))
    shader.uniform_float("borderColor", _to_linear(border_color, alpha_mult))
    batch.draw(shader)


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
    blf.size(font_id, hud_layout.FONT_SIZE)
    center_x = context.region.width // 2

    for row, entry in enumerate(sorted(entries, key=lambda e: e["slot"])):
        text = entry["text"]
        color = entry["color"]
        icon_texture = entry.get("icon_texture")
        text_w, text_h = blf.dimensions(font_id, text)
        cy = hud_layout.row_y(row)

        icon_advance = (_HUD_SLOT_ICON_SIZE + _HUD_SLOT_ICON_GAP) if icon_texture is not None else 0
        cx = center_x - int(icon_advance + text_w) // 2
        text_x = cx + icon_advance

        x0 = cx - _HUD_SLOT_BG_PAD_X
        y0 = cy - _HUD_SLOT_BG_PAD_Y
        x1 = text_x + text_w + _HUD_SLOT_BG_PAD_X
        y1 = cy + text_h + _HUD_SLOT_BG_PAD_Y

        gpu.state.blend_set('ALPHA')
        _draw_hud_slot_badge(x0, y0, x1, y1, color)

        if icon_texture is not None:
            icon_y = cy + (text_h - _HUD_SLOT_ICON_SIZE) / 2.0
            _draw_hud_slot_icon(cx, icon_y, _HUD_SLOT_ICON_SIZE, icon_texture, color)

        gpu.state.blend_set('NONE')

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
    blf.size(font_id, hud_layout.FONT_SIZE)
    text = entry["text"]
    alpha = entry["alpha"]
    color = entry["color"]
    text_w, text_h = blf.dimensions(font_id, text)

    cx = context.region.width // 2 - int(text_w) // 2
    cy = hud_layout.row_y(len(HudSlotRegistry.get_active_entries()))

    x0 = cx - _HUD_SLOT_BG_PAD_X
    y0 = cy - _HUD_SLOT_BG_PAD_Y
    x1 = cx + text_w + _HUD_SLOT_BG_PAD_X
    y1 = cy + text_h + _HUD_SLOT_BG_PAD_Y

    gpu.state.blend_set('ALPHA')
    _draw_hud_slot_badge(x0, y0, x1, y1, color, alpha_mult=alpha)
    gpu.state.blend_set('NONE')

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
    """Stateless macro-dispatcher for viewport redraws and the shared HUD."""

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
