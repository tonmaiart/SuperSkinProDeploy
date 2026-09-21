"""Weight Brush geometry helpers."""

from collections import deque

import numpy as np

from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

_BRUSH_MAX_HOPS = 128

SCREEN_RADIUS_PX_SCALE = 500.0


def screen_mode_radius_px(brush_radius: float) -> float:
    """Screen projection's brush_radius -> on-screen pixels, independent of view
    zoom/distance/depth."""
    return brush_radius * SCREEN_RADIUS_PX_SCALE


def _bfs_within_world_radius(v_idx, coords, adjacency, matrix_world, radius, max_hops):
    """Walk the 1-ring adjacency graph, accumulating TRUE world-space edge length (each visited
    vertex's local."""
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


def get_posed_vertex_coordinates(context, obj):
    """Per-vertex LOCAL-space coordinates reflecting the object's CURRENT pose."""
    coords = None
    try:
        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        eval_mesh = eval_obj.to_mesh()
        try:
            coords = [tuple(v.co) for v in eval_mesh.vertices]
        finally:
            eval_obj.to_mesh_clear()
    except Exception:
        coords = None

    if coords is None or len(coords) != len(obj.data.vertices):
        return [tuple(v.co) for v in obj.data.vertices]
    return coords


def _polygon_vertex_lists(mesh) -> list:
    """Per-polygon vertex index lists via bulk reads."""
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
    hidden = np.zeros(len(mesh.polygons), dtype=np.bool_)
    if len(hidden):
        mesh.polygons.foreach_get("hide", hidden)
    return hidden


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
    """Build a BVHTree over the object's CURRENT POSE (`get_posed_vertex_coordinates()`) but
    the live edit-mesh's own face TOPOLOGY (`bmesh.from_edit_mesh()`). Hidden polygons are skipped."""
    mesh = obj.data
    posed_coords = get_posed_vertex_coordinates(context, obj)
    bvh = build_visible_bvh(posed_coords, mesh)
    return mesh, bvh, posed_coords


def raycast_under_cursor(context, event, obj, bvh):
    """Cast a ray from the viewport camera through the cursor into *bvh* (see `build_bvh()`)."""
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return False, None, None, None

    coord = (event.mouse_region_x, event.mouse_region_y)
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


def gather_brush_vertices(core_facade, bm, face_index, hit_location_world, matrix_world, radius, posed_coords):
    """Vertices within *radius* (true world-space geodesic distance) of whichever hit face's
    own vertex sits closest to *hit_location_world*."""
    if face_index is None or face_index < 0 or face_index >= len(bm.polygons):
        return {}

    face_verts = bm.polygons[face_index].vertices
    start_v = min(
        face_verts,
        key=lambda v: (matrix_world @ Vector(posed_coords[v]) - hit_location_world).length_squared,
    )

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
    hidden = np.zeros(len(mesh.vertices), dtype=np.bool_)
    if len(hidden):
        mesh.vertices.foreach_get("hide", hidden)
    return hidden


def build_screen_kdtree(matrix_world, region, rv3d, posed_coords, hidden_vertices=None):
    """Build a `mathutils.kdtree.KDTree` of every visible vertex's 2D SCREEN-space projection
    (region-relative pixels, Z = 0). Vertices flagged in *hidden_vertices* are skipped."""
    kd = KDTree(len(posed_coords))
    for i, co in enumerate(posed_coords):
        if hidden_vertices is not None and hidden_vertices[i]:
            continue
        world_co = matrix_world @ Vector(co)
        co_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, world_co)
        if co_2d is None:
            continue
        kd.insert((co_2d.x, co_2d.y, 0.0), i)
    kd.balance()
    return kd


def gather_brush_vertices_screen(kdtree_2d, center_2d, radius_px, world_radius):
    """Screen-space "pass through" vertex gathering."""
    if radius_px <= 1e-9:
        return {}
    cx, cy = center_2d[0], center_2d[1]
    return {
        v_idx: (dist_px / radius_px) * world_radius
        for _co, v_idx, dist_px in kdtree_2d.find_range((cx, cy, 0.0), radius_px)
    }
