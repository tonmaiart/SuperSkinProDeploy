"""Weight Brush cursor -- outer (radius) + inner (falloff start) ring, drawn at the brush's
current position, plus a text label."""

import math

import bpy
import gpu
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

_draw_handle_pixel = None
_draw_handle_view = None
_active = False
_mode = None  # 'SCREEN' or 'SURFACE' while _active, else None

# SCREEN mode state -- literal 2D pixel coordinates.
_center_2d = (0.0, 0.0)
_radius_px = 0.0
_falloff_edge_px = 0.0

# SURFACE mode state -- true world-space coordinates/units.
_center_world = None
_normal_world = None
_radius_world = 0.0
_falloff_edge_world = 0.0

_label = ""

_SEGMENTS = 48
_OUTER_COLOR = (1.0, 0.1, 0.1, 0.9)
_INNER_COLOR = (1.0, 0.1, 0.1, 0.35)
_TEXT_COLOR = (1.0, 0.1, 0.1, 1.0)


def _circle_points_2d(center, radius):
    cx, cy = center
    return [
        (
            cx + radius * math.cos(2.0 * math.pi * i / _SEGMENTS),
            cy + radius * math.sin(2.0 * math.pi * i / _SEGMENTS),
        )
        for i in range(_SEGMENTS + 1)
    ]


def _tangent_basis(normal):
    """An arbitrary orthonormal (t1, t2) basis spanning the plane perpendicular to *normal*."""
    a = Vector((1.0, 0.0, 0.0)) if abs(normal.x) < 0.9 else Vector((0.0, 1.0, 0.0))
    t1 = normal.cross(a).normalized()
    t2 = normal.cross(t1).normalized()
    return t1, t2


def _ring_points_world(center, t1, t2, radius):
    return [
        center + radius * (math.cos(a) * t1 + math.sin(a) * t2)
        for a in (2.0 * math.pi * i / _SEGMENTS for i in range(_SEGMENTS + 1))
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


def _project_surface_label_anchor():
    """2D screen position for the label while in Surface mode."""
    context = bpy.context
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None or _center_world is None:
        return None
    co2d = view3d_utils.location_3d_to_region_2d(region, rv3d, _center_world)
    return (co2d.x, co2d.y) if co2d is not None else None


def _draw_callback_pixel():
    if not _active:
        return

    if _mode == 'SCREEN':
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind()
        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(1.5)

        if _falloff_edge_px < _radius_px - 0.5:
            shader.uniform_float("color", _INNER_COLOR)
            batch_for_shader(
                shader, 'LINE_STRIP', {"pos": _circle_points_2d(_center_2d, _falloff_edge_px)},
            ).draw(shader)

        shader.uniform_float("color", _OUTER_COLOR)
        batch_for_shader(
            shader, 'LINE_STRIP', {"pos": _circle_points_2d(_center_2d, _radius_px)},
        ).draw(shader)

        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')
        cx, cy = _center_2d

        if _label:
            _draw_text_below(cx, cy - _radius_px - 18.0, _label, _TEXT_COLOR)

    elif _mode == 'SURFACE':
        if not _label:
            return
        anchor = _project_surface_label_anchor()
        if anchor is None:
            return
        cx, cy = anchor
        _draw_text_below(cx, cy - 30.0, _label, _TEXT_COLOR)


def _draw_callback_view():
    if not _active or _mode != 'SURFACE' or _center_world is None or _normal_world is None:
        return

    t1, t2 = _tangent_basis(_normal_world)

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    gpu.state.line_width_set(1.5)

    if _falloff_edge_world < _radius_world - 1e-6:
        shader.uniform_float("color", _INNER_COLOR)
        batch_for_shader(
            shader, 'LINE_STRIP',
            {"pos": _ring_points_world(_center_world, t1, t2, _falloff_edge_world)},
        ).draw(shader)

    shader.uniform_float("color", _OUTER_COLOR)
    batch_for_shader(
        shader, 'LINE_STRIP',
        {"pos": _ring_points_world(_center_world, t1, t2, _radius_world)},
    ).draw(shader)

    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


def _ensure_handlers():
    global _draw_handle_pixel, _draw_handle_view
    if _draw_handle_pixel is None:
        _draw_handle_pixel = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback_pixel, (), 'WINDOW', 'POST_PIXEL',
        )
    if _draw_handle_view is None:
        _draw_handle_view = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback_view, (), 'WINDOW', 'POST_VIEW',
        )


def show_screen(center_2d, radius_px, falloff_edge_px, label):
    """Screen-projection cursor."""
    global _mode, _center_2d, _radius_px, _falloff_edge_px, _label, _active
    _mode = 'SCREEN'
    _center_2d = center_2d
    _radius_px = radius_px
    _falloff_edge_px = falloff_edge_px
    _label = label
    _active = True
    _ensure_handlers()
    _tag_redraw()


def show_surface(center_world, normal_world, radius_world, falloff_edge_world, label):
    """Surface-projection cursor."""
    global _mode, _center_world, _normal_world, _radius_world, _falloff_edge_world, _label, _active
    _mode = 'SURFACE'
    _center_world = center_world
    _normal_world = normal_world
    _radius_world = radius_world
    _falloff_edge_world = falloff_edge_world
    _label = label
    _active = True
    _ensure_handlers()
    _tag_redraw()


def hide():
    global _draw_handle_pixel, _draw_handle_view, _active, _mode
    if _draw_handle_pixel is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle_pixel, 'WINDOW')
        _draw_handle_pixel = None
    if _draw_handle_view is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle_view, 'WINDOW')
        _draw_handle_view = None
    _active = False
    _mode = None
    _tag_redraw()


def cleanup():
    """Defensive unregister-time cleanup."""
    hide()


def _tag_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
