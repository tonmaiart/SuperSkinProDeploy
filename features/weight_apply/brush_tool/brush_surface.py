
import math

import numpy as np

from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .. import evaluated_map, mesh_flags


class PosedCoords:
    """Read-only vertex coordinates: a float64 `(N, 3)` array in `array`, and the same values as
    `[x, y, z]` rows, which are built only when something walks or bulk-reads them (the BVH)."""
    __slots__ = ("array", "_rows")

    def __init__(self, array):
        self.array = array
        self._rows = None

    def rows(self) -> list:
        if self._rows is None:
            self._rows = self.array.tolist()
        return self._rows

    def __len__(self):
        return len(self.array)

    def __getitem__(self, index):
        if self._rows is not None:
            return self._rows[index]
        return self.array[index].tolist()

    def __iter__(self):
        return iter(self.rows())


def posed_array(coords):
    if isinstance(coords, PosedCoords):
        return coords.array
    return np.asarray(coords, dtype=np.float64).reshape(-1, 3)


def _coord_rows(coords):
    return coords.rows() if isinstance(coords, PosedCoords) else coords


def rest_positions(mesh):
    return mesh_flags.vertex_positions(mesh).astype(np.float64)


def _vertex_coord_tuples(mesh) -> PosedCoords:
    return PosedCoords(rest_positions(mesh))


def get_posed_vertex_coordinates(context, obj):
    try:
        coords = evaluated_map.displayed(obj, context.evaluated_depsgraph_get()).coords
    except Exception:
        return _vertex_coord_tuples(obj.data)
    return PosedCoords(np.asarray(coords, dtype=np.float64).reshape(-1, 3))


class MeshTopology:
    """Face corners of a mesh as flat arrays, plus what is derived from them on first use: the
    per-polygon vertex lists the BVH takes and each vertex's corners."""

    def __init__(self, corners, starts, totals, vert_count):
        self.corners = corners
        self.starts = starts
        self.totals = totals
        self.vert_count = vert_count
        self._polygons = None
        self._poly_of_corner = None
        self._next_corner = None
        self._vert_corners = None
        self._triangles = None

    def polygons(self) -> list:
        if self._polygons is None:
            bounds = np.append(self.starts, len(self.corners)).tolist()
            corner_list = self.corners.tolist()
            self._polygons = [corner_list[bounds[i]:bounds[i + 1]] for i in range(len(self.starts))]
        return self._polygons

    def poly_of_corner(self):
        if self._poly_of_corner is None:
            self._poly_of_corner = np.repeat(np.arange(len(self.starts), dtype=np.int64), self.totals)
        return self._poly_of_corner

    def next_corner(self):
        if self._next_corner is None:
            nxt = np.arange(1, len(self.corners) + 1, dtype=np.int64)
            nxt[self.starts + self.totals - 1] = self.starts
            self._next_corner = nxt
        return self._next_corner

    def triangles(self, mesh):
        rest_key = hash(mesh_flags.vertex_positions(mesh).tobytes())
        if self._triangles is None or self._triangles[0] != rest_key:
            count = len(mesh.loop_triangles)
            tri_corners = np.empty(count * 3, dtype=np.intc)
            mesh.loop_triangles.foreach_get("loops", tri_corners)
            tris = self.corners[tri_corners]
            faces = np.empty(count, dtype=np.intc)
            tri_faces = getattr(mesh, "loop_triangle_polygons", None)
            if tri_faces is not None and len(tri_faces) == count:
                tri_faces.foreach_get("value", faces)
            else:
                mesh.loop_triangles.foreach_get("polygon_index", faces)
            self._triangles = (rest_key, tris.astype(np.int64), faces.astype(np.int64))
        return self._triangles[1], self._triangles[2]

    def vert_corners(self):
        if self._vert_corners is None:
            order = np.argsort(self.corners, kind="stable")
            counts = np.bincount(self.corners, minlength=self.vert_count)
            self._vert_corners = (np.concatenate(([0], np.cumsum(counts))).astype(np.int64), order)
        return self._vert_corners


def _spans(starts, counts):
    total = int(counts.sum())
    return np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts) + np.repeat(starts, counts)


_topology_cache = None


def mesh_topology(mesh) -> MeshTopology:
    global _topology_cache
    corners = np.empty(len(mesh.loops), dtype=np.intc)
    corner_vert = mesh.attributes.get(".corner_vert")
    if corner_vert is not None and corner_vert.data_type == 'INT' and len(corner_vert.data) == len(corners):
        corner_vert.data.foreach_get("value", corners)
    else:
        mesh.loops.foreach_get("vertex_index", corners)
    key = (len(mesh.vertices), len(mesh.polygons), hash(corners.tobytes()))
    cached = _topology_cache
    if cached is not None and cached[0] == key:
        return cached[1]
    starts = np.empty(len(mesh.polygons), dtype=np.intc)
    mesh.polygons.foreach_get("loop_start", starts)
    totals = np.diff(np.append(starts, len(corners)))
    topology = MeshTopology(corners.astype(np.int64), starts.astype(np.int64), totals.astype(np.int64),
                            len(mesh.vertices))
    _topology_cache = (key, topology)
    return topology


def _polygon_vertex_lists(mesh) -> list:
    try:
        return mesh_topology(mesh).polygons()
    except Exception:
        return [list(p.vertices) for p in mesh.polygons]


def polygon_hide_flags(mesh):
    return mesh_flags.polygon_hide(mesh)


class _VisibleFaceBVH:
    """BVHTree over visible polygons only; `ray_cast` and `find_nearest` report the original
    polygon index."""

    def __init__(self, coords, polygons, hidden):
        visible = np.flatnonzero(~hidden)
        self._face_map = visible.tolist()
        self._tree = BVHTree.FromPolygons(_coord_rows(coords), [polygons[i] for i in self._face_map])

    def ray_cast(self, origin, direction, distance=None):
        hit = self._tree.ray_cast(origin, direction) if distance is None else self._tree.ray_cast(origin, direction, distance)
        location, normal, index, dist = hit
        if location is None:
            return hit
        return location, normal, self._face_map[index], dist

    def find_nearest(self, origin, distance=None):
        hit = self._tree.find_nearest(origin) if distance is None else self._tree.find_nearest(origin, distance)
        location, normal, index, dist = hit
        if location is None:
            return hit
        return location, normal, self._face_map[index], dist


class SurfaceBVH:
    """The visible faces in Rust, answering `ray_cast`/`find_nearest` as `mathutils` BVHTree does;
    *source* names the triangles, so a refit can tell they still hold."""
    __slots__ = ("_native", "source")

    def __init__(self, native, source):
        self._native = native
        self.source = source

    def refitted(self, coords):
        return SurfaceBVH(self._native.refitted(posed_array(coords).ravel()), self.source)

    @staticmethod
    def _answer(hit):
        if hit is None:
            return None, None, None, None
        location, normal, face, dist = hit
        return Vector(location), Vector(normal), face, dist

    def ray_cast(self, origin, direction, distance=None):
        return self._answer(self._native.ray_cast(tuple(origin), tuple(direction), distance))

    def find_nearest(self, origin, distance=None):
        return self._answer(self._native.find_nearest(tuple(origin), distance))


def _surface_triangles(mesh):
    tris, faces = mesh_topology(mesh).triangles(mesh)
    hidden = polygon_hide_flags(mesh)
    source = (tris, hash(hidden.tobytes()))
    if hidden.any():
        keep = ~hidden[faces]
        tris, faces = tris.reshape(-1, 3)[keep].ravel(), faces[keep]
    return source, tris, faces


def _surface_bvh(coords, mesh, previous=None):
    from ....core.facade import CoreFacade
    source, tris, faces = _surface_triangles(mesh)
    if (isinstance(previous, SurfaceBVH) and previous.source[0] is source[0]
            and previous.source[1] == source[1]):
        return previous.refitted(coords)
    native = CoreFacade.get_rust_gateway("surface_bvh").call(
        "rust_surface_bvh", posed_array(coords).ravel(), tris, faces,
    )
    return SurfaceBVH(native, source)


def build_visible_bvh(coords, mesh, previous=None):
    try:
        return _surface_bvh(coords, mesh, previous)
    except (AttributeError, RuntimeError, ValueError):
        pass
    polygons = _polygon_vertex_lists(mesh)
    hidden = polygon_hide_flags(mesh)
    if not hidden.any():
        return BVHTree.FromPolygons(_coord_rows(coords), polygons)
    return _VisibleFaceBVH(coords, polygons, hidden)


_surface_cache = None


def build_bvh(context, obj):
    global _surface_cache
    from .brush_prep import get_shape_gen
    mesh = obj.data
    context.evaluated_depsgraph_get()
    key = (obj.name, mesh.session_uid, len(mesh.vertices), len(mesh.polygons),
           hash(polygon_hide_flags(mesh).tobytes()))
    cached = _surface_cache
    if cached is not None and cached[0] == key and cached[1] == get_shape_gen():
        return mesh, cached[2], cached[3]
    posed_coords = get_posed_vertex_coordinates(context, obj)
    if (cached is not None and cached[0] == key
            and np.array_equal(posed_array(cached[3]), posed_array(posed_coords))):
        _surface_cache = (key, get_shape_gen(), cached[2], cached[3])
        return mesh, cached[2], cached[3]
    bvh = build_visible_bvh(posed_coords, mesh, cached[2] if cached is not None and cached[0] == key else None)
    _surface_cache = (key, get_shape_gen(), bvh, posed_coords)
    return mesh, bvh, posed_coords


def drop_surface_cache():
    global _surface_cache, _topology_cache
    _surface_cache = None
    _topology_cache = None


def _vertex_normals(topology, co, inv, verts):
    vert_offsets, vert_order = topology.vert_corners()
    counts = vert_offsets[verts + 1] - vert_offsets[verts]
    corners_of = vert_order[_spans(vert_offsets[verts], counts)]
    poly_of = topology.poly_of_corner()
    polys = np.unique(poly_of[corners_of])
    poly_counts = topology.totals[polys]
    poly_corners = _spans(topology.starts[polys], poly_counts)
    lv = topology.corners
    edges = np.cross(co[lv[poly_corners]], co[lv[topology.next_corner()[poly_corners]]])
    local_poly = np.repeat(np.arange(len(polys)), poly_counts)
    face_n = np.stack([np.bincount(local_poly, edges[:, k], len(polys)) for k in range(3)], axis=1)
    per_corner = face_n[np.searchsorted(polys, poly_of[corners_of])]
    local_vert = np.repeat(np.arange(len(verts)), counts)
    normals = np.stack([np.bincount(local_vert, per_corner[:, k], len(verts)) for k in range(3)], axis=1)
    normals = normals @ inv
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return normals / np.maximum(length, 1e-12)


class LazyVertexNormals:
    """`posed_vertex_normals()`, filled in only for the vertices indexed, so a stroke on a dense
    mesh never pays for the whole surface. Index with an integer array."""

    def __init__(self, mesh, posed_coords, matrix_world):
        self._topology = mesh_topology(mesh)
        self._co = posed_array(posed_coords)
        self._inv = np.linalg.inv(np.array(matrix_world, dtype=np.float64)[:3, :3])
        self._rows = np.zeros((len(self._co), 3))
        self._done = np.zeros(len(self._co), dtype=bool)

    def __getitem__(self, ids):
        ids = np.asarray(ids, dtype=np.int64)
        todo = np.unique(ids[~self._done[ids]])
        if len(todo):
            self._rows[todo] = _vertex_normals(self._topology, self._co, self._inv, todo)
            self._done[todo] = True
        return self._rows[ids]

    def full(self):
        return self[np.arange(len(self._co))]


def posed_vertex_normals(mesh, posed_coords, matrix_world):
    return LazyVertexNormals(mesh, posed_coords, matrix_world).full()


def welded_normals(normals, welds):
    ids = np.fromiter(welds.keys(), dtype=np.int64, count=len(welds))
    reps = np.fromiter(welds.values(), dtype=np.int64, count=len(welds))
    sums = np.zeros_like(normals)
    np.add.at(sums, reps, normals[ids])
    out = normals.copy()
    shared = sums[reps]
    out[ids] = shared / np.maximum(np.linalg.norm(shared, axis=1, keepdims=True), 1e-12)
    return out


def faces_view(normal, point, eye, view_dir):
    d = (np.asarray(point, dtype=np.float64) - eye) if eye is not None else view_dir
    return float(np.dot(normal, d)) <= 0.0


def facing_mask(ids, normals, world, eye, view_dir, front):
    d = (world[ids] - eye) if eye is not None else np.asarray(view_dir, dtype=np.float64)
    rows = normals[ids]
    dots = np.einsum("ij,ij->i", rows, np.broadcast_to(d, rows.shape))
    return dots <= 0.0 if front else dots >= 0.0


REST_SIDE_COS = 0.5


def stroke_side_mask(ids, normals, world, eye, view_dir, front, rest_normals=None, reference=None):
    keep = facing_mask(ids, normals, world, eye, view_dir, front)
    if rest_normals is None or reference is None or keep.all():
        return keep
    reference = np.asarray(reference, dtype=np.float64)
    length = float(np.linalg.norm(reference))
    if length <= 1e-12:
        return keep
    loose = ids[~keep]
    keep[~keep] = rest_normals[loose] @ (reference / length) >= REST_SIDE_COS
    return keep


def keep_facing(gathered, normals, world, eye, view_dir, front):
    if not gathered:
        return gathered
    ids = np.fromiter(gathered, dtype=np.int64, count=len(gathered))
    dist = np.fromiter(gathered.values(), dtype=np.float64, count=len(gathered))
    keep = facing_mask(ids, normals, world, eye, view_dir, front)
    return dict(zip(ids[keep].tolist(), dist[keep].tolist()))


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


_NEAREST_SAMPLES = 32
_NEAREST_REFINE = 16


def nearest_on_ray(bvh, origin, direction, box_min, box_max):
    origin, direction = np.asarray(origin, dtype=np.float64), np.asarray(direction, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = (np.asarray(box_min) - origin) / direction
        t2 = (np.asarray(box_max) - origin) / direction
    lo = np.nan_to_num(np.minimum(t1, t2), nan=-np.inf)
    hi = np.nan_to_num(np.maximum(t1, t2), nan=np.inf)
    near, far = max(0.0, float(lo.max())), float(hi.min())
    if far < near:
        return None

    def probe(t):
        point = origin + direction * t
        location, normal, face_index, gap = bvh.find_nearest(Vector(point))
        return (math.inf, None) if location is None else (gap, (location, normal, face_index, gap))

    step = (far - near) / _NEAREST_SAMPLES
    best_t = min((near + step * (i + 0.5) for i in range(_NEAREST_SAMPLES)), key=lambda t: probe(t)[0])
    lo_t, hi_t = max(near, best_t - step), min(far, best_t + step)
    for _ in range(_NEAREST_REFINE):
        a, b = lo_t + (hi_t - lo_t) / 3.0, hi_t - (hi_t - lo_t) / 3.0
        if probe(a)[0] < probe(b)[0]:
            hi_t = b
        else:
            lo_t = a
    return probe((lo_t + hi_t) / 2.0)[1]


_key_memo = (None, None, None)


def rest_key(coords):
    return _rest_memo(coords)[1]


def rest_array(coords):
    return _rest_memo(coords)[2]


def _rest_memo(coords):
    global _key_memo
    if _key_memo[0] is not coords:
        flat = posed_array(coords)
        _key_memo = (coords, (flat.shape, hash(flat.tobytes())), flat)
    return _key_memo


def vertex_hide_flags(mesh):
    return mesh_flags.vertex_hide(mesh)
