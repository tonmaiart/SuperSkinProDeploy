
import math
from array import array
from collections import deque
from itertools import chain

import numpy as np

from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from .. import mesh_flags

_BRUSH_MAX_HOPS = 128

SCREEN_RADIUS_PX_SCALE = 500.0


def screen_mode_radius_px(brush_radius: float) -> float:
    return brush_radius * SCREEN_RADIUS_PX_SCALE


def _bfs_within_world_radius(v_idx, coords, adjacency, matrix_world, radius, max_hops):
    world_cache = {}

    walk_limit = radius * math.sqrt(2.0)

    def world_co(i):
        co = world_cache.get(i)
        if co is None:
            co = matrix_world @ Vector(coords[i])
            world_cache[i] = co
        return co

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
            if new_dist > walk_limit:
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
    """BVHTree over visible polygons only; `ray_cast` and `find_nearest` report the original
    polygon index."""

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

    def find_nearest(self, origin, distance=None):
        hit = self._tree.find_nearest(origin) if distance is None else self._tree.find_nearest(origin, distance)
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


_key_memo = (None, None)


def rest_key(coords):
    global _key_memo
    if _key_memo[0] is coords:
        return _key_memo[1]
    flat = np.asarray(coords, dtype=np.float64)
    key = (flat.shape, hash(flat.tobytes()))
    _key_memo = (coords, key)
    return key


_csr_cache = None


def _adjacency_csr(adjacency, vert_count):
    global _csr_cache
    cached = _csr_cache
    if cached is not None and cached[0] is adjacency and cached[1] == vert_count:
        return cached[2], cached[3]
    rows = [adjacency.get(v, ()) for v in range(vert_count)]
    counts = np.fromiter(map(len, rows), dtype=np.int64, count=vert_count)
    offsets = array("q", np.concatenate(([0], np.cumsum(counts))).astype(np.int64).tobytes())
    targets = array("q", np.fromiter(chain.from_iterable(rows), dtype=np.int64, count=int(counts.sum())).tobytes())
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
    dists = {
        v_idx: (matrix_world @ Vector(posed_coords[v_idx]) - hit_location_world).length
        for v_idx in set(reached) | {start_v}
    }
    return {v: d for v, d in dists.items() if d <= radius or v == start_v}


def vertex_hide_flags(mesh):
    return mesh_flags.vertex_hide(mesh)


def build_world_points(matrix_world, posed_coords, hidden_vertices=None):
    m = np.array(matrix_world, dtype=np.float64)
    co = np.asarray(posed_coords, dtype=np.float64).reshape(-1, 3)
    if hidden_vertices is not None:
        ids = np.flatnonzero(~np.asarray(hidden_vertices, dtype=bool))
    else:
        ids = np.arange(len(co))
    return ids, co[ids] @ m[:3, :3].T + m[:3, 3]


def gather_brush_vertices_volume(world_points, center_world, radius):
    if radius <= 1e-9:
        return {}
    ids, xyz = world_points
    d = np.linalg.norm(xyz - np.asarray(center_world, dtype=np.float64), axis=1)
    inside = np.flatnonzero(d <= radius)
    return dict(zip(ids[inside].tolist(), d[inside].tolist()))


def keep_connected(gathered, seeds, adjacency):
    reached = {v for v in seeds if v in gathered}
    stack = list(reached)
    while stack:
        v = stack.pop()
        for n in adjacency.get(v, ()):
            if n in gathered and n not in reached:
                reached.add(n)
                stack.append(n)
    return {v: gathered[v] for v in reached}


def keep_connected_from(gathered, previous, adjacency):
    allowed = dict.fromkeys(previous, 0.0)
    allowed.update(gathered)
    reached = keep_connected(allowed, previous, adjacency)
    return {v: gathered[v] for v in reached if v in gathered}


LAYER_LINK_MAX_EDGES = 6.0

_links_cache = None


def layer_links(core_facade, posed_coords, max_dist):
    global _links_cache
    adjacency = core_facade.get_cached_mesh_neighbors()
    cached = _links_cache
    key = rest_key(posed_coords)
    if cached is not None and cached[0] == key and cached[1] is adjacency and cached[2] == max_dist:
        return cached[3]
    offsets, targets = _adjacency_csr(adjacency, len(posed_coords))
    coords = array("d", chain.from_iterable(posed_coords))
    out_offsets, out_targets = core_facade.get_rust_gateway("layer_links").call(
        "rust_layer_links", coords, offsets, targets, float(max_dist), LAYER_LINK_MAX_EDGES,
    )
    links = {
        v: out_targets[out_offsets[v]:out_offsets[v + 1]]
        for v in range(len(out_offsets) - 1) if out_offsets[v + 1] > out_offsets[v]
    }
    _links_cache = (key, adjacency, max_dist, links)
    return links


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


REACH_MAX_DETOUR = 2.0
REACH_NEAR_EDGES = 2.0

_reach_cache = None


def _links_csr(links, vert_count):
    if not links:
        return None, None
    offsets = array("q", [0]) * (vert_count + 1)
    targets = array("q")
    for v in range(vert_count):
        targets.extend(links.get(v, ()))
        offsets[v + 1] = len(targets)
    return offsets, targets


def build_reach_graph(core_facade, rest_coords, links=None):
    global _reach_cache
    adjacency = core_facade.get_cached_mesh_neighbors()
    cached = _reach_cache
    key = rest_key(rest_coords)
    if cached is not None and cached[0] == key and cached[1] is adjacency and cached[2] is links:
        return cached[3]
    offsets, targets = _adjacency_csr(adjacency, len(rest_coords))
    link_offsets, link_targets = _links_csr(links, len(rest_coords))
    coords = array("d", chain.from_iterable(rest_coords))
    graph = core_facade.get_rust_gateway("brush_reach").call(
        "rust_reach_graph", coords, offsets, targets, link_offsets, link_targets, REACH_NEAR_EDGES,
    )
    _reach_cache = (key, adjacency, links, graph)
    return graph


_weld_cache = None
_WELD_EDGE_SHARE = 0.05


def _median_edge(coords, adjacency):
    ends = [(v, u) for v in range(0, len(coords), max(1, len(coords) // 2000)) for u in adjacency.get(v, ())]
    if not ends:
        return 0.0
    ends = np.asarray(ends, dtype=np.int64)
    return float(np.median(np.linalg.norm(coords[ends[:, 0]] - coords[ends[:, 1]], axis=1)))


def open_edges(mesh):
    edge_verts = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", edge_verts)
    loop_edges = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("edge_index", loop_edges)
    faces = np.bincount(loop_edges, minlength=len(mesh.edges))
    pairs = np.sort(edge_verts.reshape(-1, 2)[faces == 1], axis=1)
    on_rim = np.zeros(len(mesh.vertices), dtype=np.bool_)
    on_rim[pairs.ravel()] = True
    return pairs, on_rim


def coincident_groups(points, candidates, tol):
    welds = {}
    if tol <= 0.0 or len(candidates) < 2:
        return welds
    tree = KDTree(len(candidates))
    for v in candidates:
        tree.insert(points[v], int(v))
    tree.balance()
    for v in candidates:
        v = int(v)
        if v in welds:
            continue
        group = [i for _co, i, _d in tree.find_range(points[v], tol)]
        if len(group) > 1:
            rep = min(group)
            for i in group:
                welds.setdefault(i, rep)
    return welds


def coincident_welds(core_facade, rest_coords):
    global _weld_cache
    adjacency = core_facade.get_cached_mesh_neighbors()
    key = rest_key(rest_coords)
    cached = _weld_cache
    if cached is not None and cached[0] == key and cached[1] is adjacency:
        return cached[2]
    coords = np.asarray(rest_coords, dtype=np.float64).reshape(-1, 3)
    _pairs, on_rim = open_edges(core_facade.get_obj().data)
    welds = coincident_groups(coords, np.flatnonzero(on_rim[:len(coords)]),
                              _median_edge(coords, adjacency) * _WELD_EDGE_SHARE)
    _weld_cache = (key, adjacency, welds)
    return welds


def weld_joined_pieces(piece_of, welds, count):
    parent = {}

    def root(p):
        while parent.get(p, p) != p:
            parent[p] = parent.get(parent[p], parent[p])
            p = parent[p]
        return p

    for v, r in (welds or {}).items():
        a, b = root(piece_of(v)), root(piece_of(r))
        if a != b:
            parent[max(a, b)] = min(a, b)
    pieces = [root(piece_of(v)) for v in range(count)]
    return pieces.__getitem__, root


def _vertex_pieces(mesh):
    ends = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", ends)
    a, b = ends[0::2], ends[1::2]
    label = np.arange(len(mesh.vertices), dtype=np.int64)
    while True:
        lo = np.minimum(label[a], label[b])
        new = label.copy()
        np.minimum.at(new, a, lo)
        np.minimum.at(new, b, lo)
        new = new[new]
        if np.array_equal(new, label):
            return label
        label = new


def _closer_piece(points, verts, dist, pieces, median):
    out = np.zeros(len(verts), dtype=np.bool_)
    check = np.flatnonzero(dist > median)
    if median <= 0.0 or not len(check):
        return out
    tree = KDTree(len(points))
    for i, co in enumerate(points.tolist()):
        tree.insert(co, i)
    tree.balance()
    for k in check.tolist():
        v = int(verts[k])
        own = pieces[v]
        out[k] = any(pieces[i] != own for _co, i, _d in tree.find_range(points[v], dist[k] - median))
    return out


_bind_cache = None
_BIND_MAX_HOPS = 4


def build_layer_bind(core_facade, rest_coords, mesh):
    global _bind_cache
    adjacency = core_facade.get_cached_mesh_neighbors()
    key = rest_key(rest_coords)
    cached = _bind_cache
    if cached is not None and cached[0] == key and cached[1] is adjacency:
        return cached[2]
    mesh.calc_loop_triangles()
    tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    coords = array("d", chain.from_iterable(rest_coords))
    corners, weights = core_facade.get_rust_gateway("layer_bind").call(
        "rust_layer_bind", coords, array("q", tris.astype(np.int64).tolist()),
    )
    points = np.asarray(rest_coords, dtype=np.float64).reshape(-1, 3)
    corners = np.asarray(corners, dtype=np.int64).reshape(-1, 3)
    weights = np.asarray(weights, dtype=np.float64).reshape(-1, 3)
    count = len(corners)
    valid = corners[:, 0] >= 0
    safe = np.where(valid[:, None], corners, 0)
    on = (weights > 1e-6) & valid[:, None]
    shared = on.sum(axis=1)
    rim_pairs, on_rim = open_edges(mesh)
    clamped = (shared == 1) & on_rim[safe[np.arange(count), on.argmax(axis=1)]]
    two = np.flatnonzero(shared == 2)
    if len(two) and len(rim_pairs):
        ends = np.sort(np.where(on[two], safe[two], -1), axis=1)[:, 1:]
        stride = len(points)
        clamped[two] |= np.isin(ends[:, 0] * stride + ends[:, 1],
                                rim_pairs[:, 0] * stride + rim_pairs[:, 1])
    bound = (weights[:, :, None] * points[safe]).sum(axis=1)
    median = _median_edge(points, adjacency)
    gap = np.linalg.norm(points[:count] - bound, axis=1)
    keep = np.flatnonzero(valid & ~(clamped & (gap > median * _WELD_EDGE_SHARE)))
    keep = keep[~_closer_piece(points, keep, gap[keep], _vertex_pieces(mesh), median)]
    by_corner = {}
    for v, c, w in zip(keep.tolist(), corners[keep].tolist(), weights[keep].tolist()):
        entry = (v, tuple(c), tuple(w))
        for corner in set(c):
            by_corner.setdefault(corner, []).append(entry)
    _bind_cache = (key, adjacency, by_corner)
    return by_corner


def carry_selected_layers(bind, selected, piece_of, leader, hidden=None):
    taken = set(selected)
    frontier = list(taken)
    for _hop in range(_BIND_MAX_HOPS):
        found = set()
        for u in frontier:
            for v, corners, weights in bind.get(u, ()):
                if v in taken or v in found or (hidden is not None and hidden[v]):
                    continue
                if piece_of(v) == leader:
                    continue
                if sum(w for c, w in zip(corners, weights) if c in taken) >= 0.5:
                    found.add(v)
        if not found:
            break
        taken |= found
        frontier = found
    return taken - set(selected)


_bind_entries_cache = None


def bind_entries(bind):
    global _bind_entries_cache
    cached = _bind_entries_cache
    if cached is not None and cached[0] is bind:
        return cached[1]
    entries = {}
    for row in bind.values():
        for v, corners, weights in row:
            entries[v] = (corners, weights)
    _bind_entries_cache = (bind, entries)
    return entries


def inner_piece(bind, verts, piece_of, points, normals, start):
    entries = bind_entries(bind)
    present = {piece_of(v) for v in verts}
    piece, seen = start, {start}
    for _hop in range(_BIND_MAX_HOPS):
        votes = {}
        for v in verts:
            if v not in entries:
                continue
            corners, weights = entries[v]
            own, other = piece_of(v), piece_of(corners[max(range(3), key=weights.__getitem__)])
            if piece not in (own, other) or own == other:
                continue
            w = np.asarray(weights, dtype=np.float64)
            c = list(corners)
            above = np.dot(points[v] - w @ points[c], w @ normals[c]) > 0.0
            lower = other if own == piece and above else own if other == piece and not above else None
            if lower is not None and lower not in seen and lower in present:
                votes[lower] = votes.get(lower, 0) + 1
        if not votes:
            break
        piece = max(votes, key=votes.get)
        seen.add(piece)
    return piece


_bind_links_cache = None


def bind_links(bind, rest_coords):
    global _bind_links_cache
    key = rest_key(rest_coords)
    cached = _bind_links_cache
    if cached is not None and cached[0] is bind and cached[1] == key:
        return cached[2]
    entries = bind_entries(bind)
    links = {}
    if entries:
        points = np.asarray(rest_coords, dtype=np.float64).reshape(-1, 3)
        verts = np.fromiter(entries.keys(), dtype=np.int64, count=len(entries))
        corners = np.array([e[0] for e in entries.values()], dtype=np.int64)
        weights = np.array([e[1] for e in entries.values()], dtype=np.float64)
        nearest = corners[np.arange(len(verts)), weights.argmax(axis=1)]
        keep = (nearest != verts) & (np.linalg.norm(points[verts] - points[nearest], axis=1) > 1e-9)
        a, b = verts[keep], nearest[keep]
        pairs = np.unique(np.stack([np.concatenate([a, b]), np.concatenate([b, a])], axis=1), axis=0)
        heads, starts = np.unique(pairs[:, 0], return_index=True)
        ends = np.append(starts[1:], len(pairs))
        for v, lo, hi in zip(heads.tolist(), starts.tolist(), ends.tolist()):
            links[v] = pairs[lo:hi, 1].tolist()
    _bind_links_cache = (bind, key, links)
    return links


def _lerp(a, b, t):
    if isinstance(a, dict):
        return {k: a.get(k, 0.0) * (1.0 - t) + b.get(k, 0.0) * t for k in set(a) | set(b)}
    return a * (1.0 - t) + b * t


def _add_scaled(a, d, s):
    if isinstance(a, dict):
        return {k: a.get(k, 0.0) + d.get(k, 0.0) * s for k in set(a) | set(d)}
    return a + d * s


def _keep_locks(row, own, locks):
    if not isinstance(row, dict):
        return min(1.0, max(0.0, row))
    row = {b: x for b, x in row.items() if x > 1e-9}
    if not locks:
        return row
    locked = {b: x for b, x in own.items() if locks.get(b)}
    free = {b: x for b, x in row.items() if not locks.get(b)}
    room = max(0.0, sum(own.values()) - sum(locked.values()))
    total = sum(free.values())
    scale = room / total if total > 1e-12 else 0.0
    return {**{b: x * scale for b, x in free.items() if x * scale > 1e-9}, **locked}


def _shift(row, old, new):
    if not isinstance(row, dict):
        return row + new - old
    shifted = {k: max(0.0, row.get(k, 0.0) + new.get(k, 0.0) - old.get(k, 0.0))
               for k in set(row) | set(old) | set(new)}
    total = sum(shifted.values())
    scale = sum(row.values()) / total if total > 1e-12 else 0.0
    return {k: x * scale for k, x in shifted.items()}


def _blend(corners, weights, row_of):
    goal = None
    for c, w in zip(corners, weights):
        part = row_of(c)
        goal = _add_scaled(goal if goal is not None else ({} if isinstance(part, dict) else 0.0), part, w)
    return goal


def lead_rows(bind, changed, rows_of, piece_of, leader, progress, locks=None, before=None):
    out = {}
    prior = before if before is not None else rows_of

    def row(v):
        return out[v] if v in out else rows_of(v)

    settled = {leader}
    frontier = {v for v in changed if piece_of(v) == leader}
    done = set()
    for _hop in range(_BIND_MAX_HOPS):
        targets = {}
        for c in frontier:
            for v, corners, weights in bind.get(c, ()):
                if v not in done and v not in targets and piece_of(v) not in settled:
                    targets[v] = (corners, weights)
        if not targets:
            break
        moved = set()
        for v, (corners, weights) in targets.items():
            t = min(1.0, progress(v, corners))
            if t <= 0.0:
                continue
            old_goal = _blend(corners, weights, prior)
            moved_row = _shift(_lerp(prior(v), old_goal, t), old_goal, _blend(corners, weights, row))
            own = rows_of(v)
            out[v] = _keep_locks(moved_row, own if isinstance(own, dict) else moved_row, locks)
            moved.add(v)
        done |= set(targets)
        settled |= {piece_of(v) for v in moved}
        frontier = moved
    return out


def carry_bound_layers(bind, gathered, radius, distance_of, world, hidden=None, piece_of=None,
                       home_piece=None):
    out = dict(gathered)
    frontier = list(gathered)
    for _hop in range(_BIND_MAX_HOPS):
        found = {}
        for u in frontier:
            for v, corners, weights in bind.get(u, ()):
                if v in out or v in found or (hidden is not None and hidden[v]):
                    continue
                if home_piece is not None and piece_of(v) == home_piece:
                    continue
                point = sum(w * world[c] for c, w in zip(corners, weights))
                d = distance_of(point)
                if d < radius:
                    found[v] = d
        if not found:
            break
        out.update(found)
        frontier = list(found)
    return out


def brush_islands(gathered, adjacency, links=None):
    left = set(gathered)
    islands = []
    while left:
        seed = left.pop()
        island = {seed}
        stack = [seed]
        while stack:
            v = stack.pop()
            for n in chain(adjacency.get(v, ()), links.get(v, ()) if links else ()):
                if n in left:
                    left.remove(n)
                    island.add(n)
                    stack.append(n)
        islands.append(island)
    return islands


def keep_reachable(graph, gathered, seeds, origin, max_reach=math.inf, piece=None):
    if not gathered:
        return {}
    kept = graph.keep_reachable(
        list(seeds), tuple(origin), list(gathered), REACH_MAX_DETOUR, float(max_reach), piece,
    )
    return {v: gathered[v] for v in kept}
