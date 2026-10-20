
from array import array
from collections import deque
from itertools import chain

import numpy as np

from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .. import mesh_flags

_BRUSH_MAX_HOPS = 128

SCREEN_RADIUS_PX_SCALE = 500.0


def screen_mode_radius_px(brush_radius: float) -> float:
    return brush_radius * SCREEN_RADIUS_PX_SCALE


def _bfs_within_world_radius(v_idx, coords, adjacency, matrix_world, radius, max_hops):
    world_cache = {}

    def world_co(i):
        co = world_cache.get(i)
        if co is None:
            co = matrix_world @ Vector(coords[i])
            world_cache[i] = co
        return co

    start_world = world_co(v_idx)
    visited = {v_idx: 0.0}
    queue = deque([(v_idx, 0.0, 0)])
    result = []
    while queue:
        cur, dist, hops = queue.popleft()
        if cur != v_idx:
            result.append(cur)
        if hops >= max_hops:
            continue
        cur_world = world_co(cur)
        for nb in adjacency.get(cur, ()):
            edge_len = (world_co(nb) - cur_world).length
            new_dist = dist + edge_len
            if new_dist > radius:
                continue
            if nb in visited and visited[nb] <= new_dist:
                continue
            visited[nb] = new_dist
            queue.append((nb, new_dist, hops + 1))
    return result


def _vertex_coord_tuples(mesh) -> list:
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    return list(map(tuple, co.reshape(-1, 3).tolist()))


_DEFORM_ONLY_MODIFIERS = frozenset({
    'ARMATURE', 'CAST', 'CORRECTIVE_SMOOTH', 'CURVE', 'DATA_TRANSFER', 'DISPLACE',
    'HOOK', 'LAPLACIANDEFORM', 'LAPLACIANSMOOTH', 'LATTICE', 'MESH_DEFORM',
    'NORMAL_EDIT', 'SHRINKWRAP', 'SIMPLE_DEFORM', 'SMOOTH', 'SURFACE_DEFORM',
    'UV_PROJECT', 'UV_WARP', 'VERTEX_WEIGHT_EDIT', 'VERTEX_WEIGHT_MIX',
    'VERTEX_WEIGHT_PROXIMITY', 'WARP', 'WAVE', 'WEIGHTED_NORMAL',
})


def _coarse_vertices_lead_evaluated_mesh(obj) -> bool:
    return all(
        m.type == 'SUBSURF' or m.type in _DEFORM_ONLY_MODIFIERS
        for m in obj.modifiers if m.show_viewport
    )


def get_posed_vertex_coordinates(context, obj):
    coords = None
    vert_count = len(obj.data.vertices)
    try:
        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        eval_mesh = eval_obj.to_mesh()
        try:
            coords = _vertex_coord_tuples(eval_mesh)
        finally:
            eval_obj.to_mesh_clear()
    except Exception:
        coords = None

    if coords is not None and len(coords) > vert_count and _coarse_vertices_lead_evaluated_mesh(obj):
        coords = coords[:vert_count]
    if coords is None or len(coords) != vert_count:
        return _vertex_coord_tuples(obj.data)
    return coords


def _polygon_vertex_lists(mesh) -> list:
    try:
        poly_count = len(mesh.polygons)
        corner_count = len(mesh.loops)
        starts = np.empty(poly_count, dtype=np.int32)
        mesh.polygons.foreach_get("loop_start", starts)
        corners = np.empty(corner_count, dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", corners)
        bounds = np.append(starts, corner_count).tolist()
        corner_list = corners.tolist()
        return [corner_list[bounds[i]:bounds[i + 1]] for i in range(poly_count)]
    except Exception:
        return [list(p.vertices) for p in mesh.polygons]


def polygon_hide_flags(mesh):
    return mesh_flags.polygon_hide(mesh)


class _VisibleFaceBVH:
    """BVHTree over visible polygons only; `ray_cast` reports the original polygon index."""

    def __init__(self, coords, polygons, hidden):
        visible = np.flatnonzero(~hidden)
        self._face_map = visible.tolist()
        self._tree = BVHTree.FromPolygons(coords, [polygons[i] for i in self._face_map])

    def ray_cast(self, origin, direction, distance=None):
        hit = self._tree.ray_cast(origin, direction) if distance is None else self._tree.ray_cast(origin, direction, distance)
        location, normal, index, dist = hit
        if location is None:
            return hit
        return location, normal, self._face_map[index], dist


def build_visible_bvh(coords, mesh):
    polygons = _polygon_vertex_lists(mesh)
    hidden = polygon_hide_flags(mesh)
    if not hidden.any():
        return BVHTree.FromPolygons(coords, polygons)
    return _VisibleFaceBVH(coords, polygons, hidden)


def build_bvh(context, obj):
    mesh = obj.data
    posed_coords = get_posed_vertex_coordinates(context, obj)
    bvh = build_visible_bvh(posed_coords, mesh)
    return mesh, bvh, posed_coords


def raycast_under_cursor(context, event, obj, bvh):
    return raycast_at(context, (event.mouse_region_x, event.mouse_region_y), obj, bvh)


def raycast_at(context, coord, obj, bvh):
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return False, None, None, None

    origin_world = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction_world = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    if origin_world is None or direction_world is None:
        return False, None, None, None

    mat_inv = obj.matrix_world.inverted()
    origin_local = mat_inv @ origin_world
    direction_local = (mat_inv.to_3x3() @ direction_world).normalized()

    location, normal_local, face_index, _distance = bvh.ray_cast(origin_local, direction_local)
    if location is None:
        return False, None, None, None

    normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
    world_normal = (normal_matrix @ normal_local).normalized()

    return True, face_index, obj.matrix_world @ location, world_normal


_csr_cache = None


def _adjacency_csr(adjacency, vert_count):
    global _csr_cache
    cached = _csr_cache
    if cached is not None and cached[0] is adjacency and cached[1] == vert_count:
        return cached[2], cached[3]
    offsets = array("q", [0]) * (vert_count + 1)
    targets = array("q")
    for v in range(vert_count):
        targets.extend(adjacency.get(v, ()))
        offsets[v + 1] = len(targets)
    _csr_cache = (adjacency, vert_count, offsets, targets)
    return offsets, targets


_graph_cache = None


def build_brush_graph(core_facade, posed_coords):
    global _graph_cache
    rust = core_facade.get_rust_gateway("brush_gather")
    adjacency = core_facade.get_cached_mesh_neighbors()
    cached = _graph_cache
    if cached is not None and cached[0] is posed_coords and cached[1] is adjacency:
        return cached[2]
    offsets, targets = _adjacency_csr(adjacency, len(posed_coords))
    coords = array("d", chain.from_iterable(posed_coords))
    graph = rust.call("rust_brush_graph", offsets, targets, coords)
    _graph_cache = (posed_coords, adjacency, graph)
    return graph


def gather_brush_vertices(core_facade, bm, face_index, hit_location_world, matrix_world, radius,
                          posed_coords, graph=None):
    if face_index is None or face_index < 0 or face_index >= len(bm.polygons):
        return {}

    face_verts = bm.polygons[face_index].vertices
    start_v = min(
        face_verts,
        key=lambda v: (matrix_world @ Vector(posed_coords[v]) - hit_location_world).length_squared,
    )

    if graph is not None:
        matrix = [matrix_world[r][c] for r in range(3) for c in range(4)]
        hit = tuple(hit_location_world)
        verts, dists = graph.gather(start_v, matrix, hit, radius, _BRUSH_MAX_HOPS)
        return dict(zip(verts, dists))

    adjacency = core_facade.get_cached_mesh_neighbors()
    reached = _bfs_within_world_radius(
        start_v, posed_coords, adjacency, matrix_world, radius, _BRUSH_MAX_HOPS,
    )
    verts = set(reached)
    verts.add(start_v)

    return {
        v_idx: (matrix_world @ Vector(posed_coords[v_idx]) - hit_location_world).length
        for v_idx in verts
    }


def vertex_hide_flags(mesh):
    return mesh_flags.vertex_hide(mesh)


def build_screen_points(matrix_world, region, rv3d, posed_coords, hidden_vertices=None):
    persp = np.array(rv3d.perspective_matrix @ matrix_world, dtype=np.float64)
    co = np.asarray(posed_coords, dtype=np.float64).reshape(-1, 3)
    prj = co @ persp[:, :3].T + persp[:, 3]
    w = prj[:, 3]
    keep = w > 0.0
    if hidden_vertices is not None:
        keep &= ~np.asarray(hidden_vertices, dtype=bool)
    ids = np.flatnonzero(keep)
    half_w = region.width / 2.0
    half_h = region.height / 2.0
    xy = np.empty((len(ids), 2), dtype=np.float64)
    xy[:, 0] = half_w + half_w * (prj[ids, 0] / w[ids])
    xy[:, 1] = half_h + half_h * (prj[ids, 1] / w[ids])
    return ids, xy


def gather_brush_vertices_screen(screen_points, center_2d, radius_px, world_radius):
    if radius_px <= 1e-9:
        return {}
    ids, xy = screen_points
    d = np.hypot(xy[:, 0] - center_2d[0], xy[:, 1] - center_2d[1])
    inside = np.flatnonzero(d <= radius_px)
    scale = world_radius / radius_px
    return dict(zip(ids[inside].tolist(), (d[inside] * scale).tolist()))
