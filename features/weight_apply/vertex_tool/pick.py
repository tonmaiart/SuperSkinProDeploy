"""Screen-space vertex picking on mesh data, shared by the vertex tool's select operators."""

import numpy as np
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .common import LASSO_TOOL_IDNAME, evaluated_local_coords

_PICK_RADIUS_PX = 14.0
_MAX_VISIBILITY_TESTS = 8

_bvh_key = None
_bvh = None


def is_select_tool_active(context):
    try:
        tool = context.workspace.tools.from_space_view3d_mode('PAINT_WEIGHT', create=False)
    except Exception:
        return False
    return bool(tool is not None and tool.idname == LASSO_TOOL_IDNAME)


def is_xray(context):
    shading = getattr(context.space_data, "shading", None)
    return bool(getattr(shading, "show_xray", False))


def occlusion_enabled(context):
    """True when only surface-visible vertices may be picked (Front Face Only on, X-ray off)."""
    if is_xray(context):
        return False
    return bool(context.window_manager.superskin_weight_apply_prefs.vertex_front_face_only)


def project(context, obj, region, rv3d):
    """Returns (local, world, sx, sy, in_front) for every vertex of the deformed mesh; hidden vertices are never in front."""
    local = evaluated_local_coords(obj, context.evaluated_depsgraph_get())
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    world = local.astype(np.float64) @ matrix[:3, :3].T + matrix[:3, 3]
    persp = np.array(rv3d.perspective_matrix, dtype=np.float64)
    clip = np.c_[world, np.ones(len(world))] @ persp.T
    w = clip[:, 3]
    hidden = np.empty(len(local), dtype=np.bool_)
    obj.data.vertices.foreach_get("hide", hidden)
    in_front = (w > 1e-9) & ~hidden
    safe_w = np.where(in_front, w, 1.0)
    sx = (clip[:, 0] / safe_w * 0.5 + 0.5) * region.width
    sy = (clip[:, 1] / safe_w * 0.5 + 0.5) * region.height
    return local, world, sx, sy, in_front


def _get_bvh(context, obj, local):
    global _bvh_key, _bvh
    key = (obj.name, len(local), len(obj.data.polygons), float(local.sum(dtype=np.float64)))
    if key != _bvh_key:
        _bvh = BVHTree.FromObject(obj, context.evaluated_depsgraph_get())
        _bvh_key = key
    return _bvh


def visible_mask(context, obj, rv3d, local, world, candidates):
    """True for each candidate vertex index that no other surface hides from the view."""
    bvh = _get_bvh(context, obj, local)
    view_inv = rv3d.view_matrix.inverted()
    eye = view_inv.translation.copy()
    forward = -view_inv.col[2].xyz.normalized()
    ortho_span = (rv3d.view_distance + max(obj.dimensions)) * 2.0 + 1.0
    perspective = rv3d.is_perspective
    inv_world = obj.matrix_world.inverted()

    keep = np.zeros(len(candidates), dtype=np.bool_)
    for n, idx in enumerate(candidates):
        target_w = Vector(world[idx])
        origin_w = eye if perspective else target_w - forward * ortho_span
        origin = inv_world @ origin_w
        offset = inv_world @ target_w - origin
        dist = offset.length
        if dist <= 0.0:
            keep[n] = True
            continue
        hit = bvh.ray_cast(origin, offset / dist, dist * (1.0 - 2e-3))
        keep[n] = hit[0] is None
    return keep


def nearest_vertex(context, obj, mouse, radius=_PICK_RADIUS_PX):
    """Index of the closest visible vertex within `radius` pixels of `mouse`, else None."""
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None or len(obj.data.vertices) == 0:
        return None
    local, world, sx, sy, in_front = project(context, obj, region, rv3d)
    dist2 = (sx - mouse[0]) ** 2 + (sy - mouse[1]) ** 2
    candidates = np.nonzero(in_front & (dist2 <= radius * radius))[0]
    if len(candidates) == 0:
        return None
    ordered = candidates[np.argsort(dist2[candidates])][:_MAX_VISIBILITY_TESTS]
    if not occlusion_enabled(context):
        return int(ordered[0])
    for idx in ordered:
        if visible_mask(context, obj, rv3d, local, world, np.array([idx]))[0]:
            return int(idx)
    return None


def vertex_under_cursor(context, obj, mouse):
    """Nearest visible vertex, falling back to the closest corner of the face under the cursor."""
    found = nearest_vertex(context, obj, mouse)
    if found is not None:
        return found
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return None
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse)
    inv = obj.matrix_world.inverted()
    origin_local = inv @ origin
    direction_local = (inv.to_3x3() @ direction).normalized()
    local = evaluated_local_coords(obj, context.evaluated_depsgraph_get())
    location, _normal, face_index, _dist = _get_bvh(context, obj, local).ray_cast(origin_local, direction_local)
    if location is None or face_index is None or face_index >= len(obj.data.polygons):
        return None
    vertices = obj.data.vertices
    corners = [v for v in obj.data.polygons[face_index].vertices if not vertices[v].hide]
    if not corners:
        return None
    return int(min(corners, key=lambda v: (Vector(local[v]) - location).length_squared))
