"""Weight Brush -- persistent hover cursor."""

import bpy

from .brush_logic import (
    raycast_under_cursor, screen_mode_radius_px, get_posed_vertex_coordinates,
    build_visible_bvh, polygon_hide_flags,
)
from . import brush_draw, brush_prep

_WEIGHT_BRUSH_TOOL_IDNAME = "superskin.weight_brush_tool"

_cache_key = None
_cache_bm = None
_cache_bvh = None


def _get_bvh_cached(context, obj):
    global _cache_key, _cache_bm, _cache_bvh
    bm = obj.data
    key = (obj.name, len(bm.vertices), len(bm.polygons), hash(polygon_hide_flags(bm).tobytes()))
    if key != _cache_key:
        _cache_bvh = build_visible_bvh(get_posed_vertex_coordinates(context, obj), bm)
        _cache_key = key
    _cache_bm = bm
    return bm, _cache_bvh


def _mouse_in_window_region(context, event):
    """True only while the mouse is genuinely inside the 3D viewport's own drawable rectangle."""
    region = context.region
    if region is None or region.type != 'WINDOW':
        return False
    return 0 <= event.mouse_region_x <= region.width and 0 <= event.mouse_region_y <= region.height


def force_hide():
    """Public escape hatch for callers OUTSIDE this module."""
    brush_draw.hide()


def _is_weight_brush_tool_active(context):
    try:
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    except Exception:
        return False
    return bool(tool is not None and tool.idname == _WEIGHT_BRUSH_TOOL_IDNAME)


def _update(context, event):
    """One self-contained update per `MOUSEMOVE`."""
    if not _is_weight_brush_tool_active(context):
        brush_draw.hide()
        return

    from .brush_ops import SUPERSKIN_OT_weight_brush, get_brush_prefs, get_falloff_start_fraction
    from .brush_radius_adjust import SUPERSKIN_OT_weight_brush_adjust_radius
    from .brush_hardness_adjust import SUPERSKIN_OT_weight_brush_adjust_hardness

    if SUPERSKIN_OT_weight_brush._stroke_active:
        return  # the paint operator's own brush_draw updates take priority

    if SUPERSKIN_OT_weight_brush_adjust_radius._adjusting_active:
        return  # the F-key radius-adjust modal's own brush_draw updates take priority

    if SUPERSKIN_OT_weight_brush_adjust_hardness._adjusting_active:
        return

    if context.area is None or context.area.type != 'VIEW_3D' or not _mouse_in_window_region(context, event):
        brush_draw.hide()
        return

    obj = context.active_object
    if context.mode != 'PAINT_WEIGHT' or obj is None or obj.type != 'MESH':
        brush_draw.hide()
        return

    if context.region_data is None:
        brush_draw.hide()
        return

    brush_prep.note_hover()

    p = get_brush_prefs()
    label = ""
    falloff_start = get_falloff_start_fraction(p)

    if p.brush_projection == 'SCREEN':
        radius_px = screen_mode_radius_px(p.brush_radius)
        brush_draw.show_screen(
            (event.mouse_region_x, event.mouse_region_y), radius_px, radius_px * falloff_start, label,
        )
        return

    bm, bvh = _get_bvh_cached(context, obj)
    hit, _face_index, hit_world, hit_normal = raycast_under_cursor(context, event, obj, bvh)
    if not hit:
        brush_draw.hide()
        return

    brush_draw.show_surface(hit_world, hit_normal, p.brush_radius, p.brush_radius * falloff_start, label)


class SUPERSKIN_OT_weight_brush_hover(bpy.types.Operator):
    """Plain, non-modal, one-shot operator."""
    bl_idname = "superskin.weight_brush_hover"
    bl_label = "Weight Brush Hover Cursor"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        try:
            _update(context, event)
        except Exception:
            pass
        return {'PASS_THROUGH'}


def register():
    bpy.utils.register_class(SUPERSKIN_OT_weight_brush_hover)


def unregister():
    try:
        bpy.utils.unregister_class(SUPERSKIN_OT_weight_brush_hover)
    except Exception:
        pass
    force_hide()
