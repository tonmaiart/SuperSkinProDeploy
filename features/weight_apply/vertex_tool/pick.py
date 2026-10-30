
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .. import mesh_flags
from .common import LASSO_TOOL_IDNAME, evaluated_local_coords

_PICK_RADIUS_PX = 14.0
_MAX_VISIBILITY_TESTS = 16
_OCCLUSION_MARGIN = 2e-5

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
    if is_xray(context):
        return False
    return bool(context.window_manager.superskin_weight_apply_prefs.vertex_front_face_only)


def _project_clip(context, obj, region, rv3d):
    local = evaluated_local_coords(obj, context.evaluated_depsgraph_get())
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    world = local.astype(np.float64) @ matrix[:3, :3].T + matrix[:3, 3]
    persp = np.array(rv3d.perspective_matrix, dtype=np.float64)
    clip = np.c_[world, np.ones(len(world))] @ persp.T
    w = clip[:, 3]
    hidden = mesh_flags.vertex_hide(obj.data)
    in_front = (w > 1e-9) & ~hidden
    safe_w = np.where(in_front, w, 1.0)
    sx = (clip[:, 0] / safe_w * 0.5 + 0.5) * region.width
    sy = (clip[:, 1] / safe_w * 0.5 + 0.5) * region.height
    view = np.array(rv3d.view_matrix, dtype=np.float64)
    depth = -(world @ view[2, :3] + view[2, 3])
    return local, world, sx, sy, in_front, depth


def project(context, obj, region, rv3d):
    return _project_clip(context, obj, region, rv3d)[:5]


def _bvh_from_mesh_polygons(mesh, local):
    starts = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    corners = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", corners)
    polygons = [p.tolist() for p in np.split(corners, starts[1:])] if len(starts) else []
    return BVHTree.FromPolygons(local.tolist(), polygons)


def _get_bvh(context, obj, local):
    global _bvh_key, _bvh
    key = (obj.name, len(local), len(obj.data.polygons), float(local.sum(dtype=np.float64)))
    if key != _bvh_key:
        depsgraph = context.evaluated_depsgraph_get()
        if len(obj.evaluated_get(depsgraph).data.polygons) == len(obj.data.polygons):
            _bvh = BVHTree.FromObject(obj, depsgraph)
        else:
            _bvh = _bvh_from_mesh_polygons(obj.data, local)
        _bvh_key = key
    return _bvh


def visible_mask(context, obj, rv3d, local, world, candidates):
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


_tri_key = None
_tri_verts = None
_tri_polys = None


def _loop_triangles(context, obj, local):
    global _tri_key, _tri_verts, _tri_polys
    mesh = obj.data
    source = obj.evaluated_get(context.evaluated_depsgraph_get()).data
    if (len(source.vertices), len(source.polygons), len(source.loops)) != (
            len(mesh.vertices), len(mesh.polygons), len(mesh.loops)):
        source = mesh
    key = (source.as_pointer(), len(mesh.vertices), len(mesh.polygons), len(mesh.loops),
           float(local.sum(dtype=np.float64)))
    if key != _tri_key:
        source.calc_loop_triangles()
        count = len(source.loop_triangles)
        verts = np.empty(count * 3, dtype=np.int32)
        source.loop_triangles.foreach_get("vertices", verts)
        polys = np.empty(count, dtype=np.int32)
        source.loop_triangles.foreach_get("polygon_index", polys)
        _tri_verts, _tri_polys, _tri_key = verts.reshape(count, 3), polys, key
    return _tri_verts, _tri_polys


def _covering(tris, sx, sy, depth, perspective, px, py):
    x, y = sx[tris], sy[tris]
    rows = np.nonzero((x.min(axis=1) <= px) & (x.max(axis=1) >= px)
                      & (y.min(axis=1) <= py) & (y.max(axis=1) >= py))[0]
    x, y = x[rows], y[rows]
    det = (y[:, 1] - y[:, 2]) * (x[:, 0] - x[:, 2]) + (x[:, 2] - x[:, 1]) * (y[:, 0] - y[:, 2])
    valid = np.abs(det) > 1e-12
    safe = np.where(valid, det, 1.0)
    a = ((y[:, 1] - y[:, 2]) * (px - x[:, 2]) + (x[:, 2] - x[:, 1]) * (py - y[:, 2])) / safe
    b = ((y[:, 2] - y[:, 0]) * (px - x[:, 2]) + (x[:, 0] - x[:, 2]) * (py - y[:, 2])) / safe
    c = 1.0 - a - b
    inside = valid & (a >= -1e-6) & (b >= -1e-6) & (c >= -1e-6)
    rows, a, b, c = rows[inside], a[inside], b[inside], c[inside]
    d = depth[tris[rows]]
    if perspective:
        surface = 1.0 / (a / d[:, 0] + b / d[:, 1] + c / d[:, 2])
    else:
        surface = a * d[:, 0] + b * d[:, 1] + c * d[:, 2]
    return rows, surface


def _front_triangles(context, obj, local, sx, sy, in_front, mouse, pad):
    tris, tri_polys = _loop_triangles(context, obj, local)
    if len(tris) == 0:
        return tris, tri_polys
    x, y = sx[tris], sy[tris]
    keep = (in_front[tris].all(axis=1)
            & (x.min(axis=1) <= mouse[0] + pad) & (x.max(axis=1) >= mouse[0] - pad)
            & (y.min(axis=1) <= mouse[1] + pad) & (y.max(axis=1) >= mouse[1] - pad))
    return tris[keep], tri_polys[keep]


def _is_unoccluded(v, tris, sx, sy, depth, perspective):
    rows, surface = _covering(tris, sx, sy, depth, perspective, sx[v], sy[v])
    others = ~(tris[rows] == v).any(axis=1)
    return not np.any(surface[others] < depth[v] - (abs(depth[v]) * _OCCLUSION_MARGIN + 1e-6))


def _frontmost_face(tris, tri_polys, sx, sy, depth, perspective, mouse):
    rows, surface = _covering(tris, sx, sy, depth, perspective, mouse[0], mouse[1])
    if len(rows) == 0:
        return None
    return int(tri_polys[rows[int(np.argmin(surface))]])


def face_under_cursor(context, obj, mouse):
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None or len(obj.data.vertices) == 0:
        return None
    local, _world, sx, sy, in_front, depth = _project_clip(context, obj, region, rv3d)
    tris, tri_polys = _front_triangles(context, obj, local, sx, sy, in_front, mouse, 0.0)
    face_index = _frontmost_face(tris, tri_polys, sx, sy, depth, rv3d.is_perspective, mouse)
    if face_index is None:
        return None
    return face_index, sx, sy, in_front


_subd_key = None
_subd_tris = None


def _subdivided_surface(context, obj, region, rv3d):
    mesh = obj.data
    count = len(mesh.vertices)
    source = obj.evaluated_get(context.evaluated_depsgraph_get()).data
    if len(source.vertices) <= count:
        return None
    from ..brush_tool.brush_logic import _coarse_vertices_lead_evaluated_mesh
    if not _coarse_vertices_lead_evaluated_mesh(obj):
        return None
    co = np.empty(len(source.vertices) * 3, dtype=np.float64)
    source.vertices.foreach_get("co", co)
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    world = co.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    clip = np.c_[world, np.ones(len(world))] @ np.array(rv3d.perspective_matrix, dtype=np.float64).T
    w = clip[:, 3]
    in_front = w > 1e-9
    in_front[:count] &= ~mesh_flags.vertex_hide(mesh)
    safe_w = np.where(in_front, w, 1.0)
    sx = (clip[:, 0] / safe_w * 0.5 + 0.5) * region.width
    sy = (clip[:, 1] / safe_w * 0.5 + 0.5) * region.height
    view = np.array(rv3d.view_matrix, dtype=np.float64)
    depth = -(world @ view[2, :3] + view[2, 3])
    global _subd_key, _subd_tris
    key = (source.as_pointer(), len(source.vertices), len(source.polygons), len(source.loops))
    if key != _subd_key:
        source.calc_loop_triangles()
        n = len(source.loop_triangles)
        tris = np.empty(n * 3, dtype=np.int32)
        source.loop_triangles.foreach_get("vertices", tris)
        polys = np.empty(n, dtype=np.int32)
        source.loop_triangles.foreach_get("polygon_index", polys)
        _subd_tris, _subd_key = (tris.reshape(n, 3), polys), key
    tris, polys = _subd_tris
    keep = in_front[tris].all(axis=1) & ~mesh_flags.polygon_hide(source)[polys]
    return tris[keep], polys[keep], sx, sy, depth


def vertex_under_cursor(context, obj, mouse):
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None or len(obj.data.vertices) == 0:
        return None
    local, _world, sx, sy, in_front, depth = _project_clip(context, obj, region, rv3d)
    perspective = rv3d.is_perspective
    tris, tri_polys = _front_triangles(context, obj, local, sx, sy, in_front, mouse, _PICK_RADIUS_PX)

    dist2 = (sx - mouse[0]) ** 2 + (sy - mouse[1]) ** 2
    candidates = np.nonzero(in_front & (dist2 <= _PICK_RADIUS_PX * _PICK_RADIUS_PX))[0]
    ordered = candidates[np.argsort(dist2[candidates])][:_MAX_VISIBILITY_TESTS]
    xray = is_xray(context)
    surface = None if xray else _subdivided_surface(context, obj, region, rv3d)
    if surface is not None:
        return _pick_on_subdivided(obj, surface, ordered, dist2, in_front, perspective, mouse)
    for idx in ordered:
        if xray or _is_unoccluded(idx, tris, sx, sy, depth, perspective):
            return int(idx)

    face_index = _frontmost_face(tris, tri_polys, sx, sy, depth, perspective, mouse)
    if face_index is None:
        return None
    corners = sorted((v for v in obj.data.polygons[face_index].vertices if in_front[v]), key=lambda v: dist2[v])
    if not corners or xray:
        return int(corners[0]) if corners else None
    all_tris, _polys = _loop_triangles(context, obj, local)
    all_tris = all_tris[in_front[all_tris].all(axis=1)]
    return next((int(v) for v in corners if _is_unoccluded(v, all_tris, sx, sy, depth, perspective)), None)


def _pick_on_subdivided(obj, surface, ordered, dist2, in_front, perspective, mouse):
    tris, tri_polys, sx, sy, depth = surface
    for idx in ordered:
        if _is_unoccluded(idx, tris, sx, sy, depth, perspective):
            return int(idx)
    face_index = _frontmost_face(tris, tri_polys, sx, sy, depth, perspective, mouse)
    if face_index is None:
        return None
    count = len(obj.data.vertices)
    face_verts = np.unique(tris[tri_polys == face_index])
    corners = [int(v) for v in face_verts if v < count and in_front[v]]
    if not corners:
        corners = np.nonzero(in_front)[0]
    corners = sorted(corners, key=lambda v: dist2[v])[:_MAX_VISIBILITY_TESTS]
    return next((int(v) for v in corners if _is_unoccluded(v, tris, sx, sy, depth, perspective)), None)
