
import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

from .brush_logic import (
    raycast_at, screen_mode_radius_px, get_posed_vertex_coordinates,
    build_visible_bvh, polygon_hide_flags,
)
from ....core.facade import CoreFacade
from . import brush_draw, brush_prep

_WEIGHT_BRUSH_TOOL_IDNAME = "superskin.weight_brush_tool"
_SURFACE_CURSOR = 'PAINT_CROSS'
_PROJECTED_CURSOR = 'NONE'

_cache_key = None
_cache_bm = None
_cache_bvh = None
_last_hit = None


def _get_bvh_cached(context, obj):
    global _cache_key, _cache_bm, _cache_bvh
    bm = obj.data
    key = (obj.name, len(bm.vertices), len(bm.polygons), hash(polygon_hide_flags(bm).tobytes()),
           brush_prep.get_pose_gen())
    if key != _cache_key:
        _cache_bvh = build_visible_bvh(get_posed_vertex_coordinates(context, obj), bm)
        _cache_key = key
    _cache_bm = bm
    return bm, _cache_bvh


def _mouse_in_window_region(context, event):
    region = context.region
    if region is None or region.type != 'WINDOW':
        return False
    return 0 <= event.mouse_region_x <= region.width and 0 <= event.mouse_region_y <= region.height


def force_hide():
    brush_draw.hide()


def sync_os_cursor(window, projection):
    if window is not None:
        window.cursor_set(_PROJECTED_CURSOR if projection == 'SCREEN' else _SURFACE_CURSOR)


def _is_weight_brush_tool_active(context):
    try:
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    except Exception:
        return False
    return bool(tool is not None and tool.idname == _WEIGHT_BRUSH_TOOL_IDNAME)


def _update(context, event):
    if not CoreFacade.is_skinning_session(context) or not _is_weight_brush_tool_active(context):
        brush_draw.hide()
        return

    from .brush_ops import SUPERSKIN_OT_weight_brush, get_brush_prefs
    from .brush_radius_adjust import SUPERSKIN_OT_weight_brush_adjust_radius

    if SUPERSKIN_OT_weight_brush._stroke_active:
        return

    if SUPERSKIN_OT_weight_brush_adjust_radius._adjusting_active:
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
    sync_os_cursor(context.window, p.brush_projection)

    if p.brush_projection == 'SCREEN':
        radius_px = screen_mode_radius_px(p.brush_radius)
        brush_draw.show_screen(
            (event.mouse_region_x, event.mouse_region_y), radius_px, label,
        )
        return

    _bm, bvh = _get_bvh_cached(context, obj)
    coord = (event.mouse_region_x, event.mouse_region_y)
    hit, _face_index, hit_world, hit_normal = raycast_at(context, coord, obj, bvh)
    if hit:
        remember_hit(obj, hit_world)
        brush_draw.show_surface(hit_world, hit_normal, p.brush_radius, label)
        return
    anchor = off_mesh_cursor(context, coord, obj)
    if anchor is None:
        brush_draw.hide()
        return
    brush_draw.show_screen(coord, off_mesh_radius_px(context, anchor[0], p.brush_radius), label,
                           color_projection=p.brush_projection)


def _in_front(rv3d, point):
    return not rv3d.is_perspective or (rv3d.view_matrix @ point).z < 0.0


def world_cursor_at(context, coord, obj, bvh):
    hit, _face_index, hit_world, hit_normal = raycast_at(context, coord, obj, bvh)
    if hit:
        remember_hit(obj, hit_world)
        return hit_world, hit_normal
    return off_mesh_cursor(context, coord, obj)


def off_mesh_radius_px(context, point, radius):
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return None
    right = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
    a = view3d_utils.location_3d_to_region_2d(region, rv3d, point)
    b = view3d_utils.location_3d_to_region_2d(region, rv3d, point + right * radius)
    return (b - a).length if a is not None and b is not None else None


def remember_hit(obj, point):
    global _last_hit
    _last_hit = (obj.name, point.copy())


def off_mesh_cursor(context, coord, obj):
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return None
    candidates = []
    if _last_hit is not None and _last_hit[0] == obj.name:
        candidates.append(_last_hit[1])
    corners = [Vector(c) for c in obj.bound_box]
    candidates.append(obj.matrix_world @ (sum(corners, Vector()) / len(corners)))
    candidates.append(rv3d.view_location)
    depth = next((c for c in candidates if _in_front(rv3d, c)), rv3d.view_location)
    point = view3d_utils.region_2d_to_location_3d(region, rv3d, coord, depth)
    if point is None:
        return None
    return point, rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))


class SUPERSKIN_OT_weight_brush_hover(bpy.types.Operator):
    """Show the brush circle under the mouse"""
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
