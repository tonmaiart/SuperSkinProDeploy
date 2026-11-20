
import math
from array import array
from collections import deque
from collections.abc import Mapping
from itertools import chain

import numpy as np

from mathutils import Vector

from .brush_surface import posed_array, rest_array, rest_key

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


_csr_cache = None


def _adjacency_csr(adjacency, vert_count):
    global _csr_cache
    cached = _csr_cache
    if cached is not None and cached[0] is adjacency and cached[1] == vert_count:
        return cached[2], cached[3]
    if len(getattr(adjacency, "offsets", ())) == vert_count + 1:
        offsets, targets = array("q", adjacency.offsets.tobytes()), array("q", adjacency.targets.tobytes())
    else:
        rows = [adjacency.get(v, ()) for v in range(vert_count)]
        counts = np.fromiter(map(len, rows), dtype=np.int64, count=vert_count)
        offsets = array("q", np.concatenate(([0], np.cumsum(counts))).astype(np.int64).tobytes())
        targets = array("q", np.fromiter(chain.from_iterable(rows), dtype=np.int64,
                                         count=int(counts.sum())).tobytes())
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
    coords = posed_array(posed_coords).ravel()
    graph = rust.call("rust_brush_graph", offsets, targets, coords)
    _graph_cache = (posed_coords, adjacency, graph)
    return graph


def _walk_start(bm, face_index, hit_location_world, matrix_world, posed_coords):
    if face_index is None or face_index < 0 or face_index >= len(bm.polygons):
        return None
    return min(
        bm.polygons[face_index].vertices,
        key=lambda v: (matrix_world @ Vector(posed_coords[v]) - hit_location_world).length_squared,
    )


def gather_many(graph, bm, hits, matrix_world, radius, posed_coords):
    starts, points, slots = [], [], []
    for i, (face_index, hit_world) in enumerate(hits):
        start = _walk_start(bm, face_index, hit_world, matrix_world, posed_coords)
        if start is not None:
            starts.append(start)
            points.extend(hit_world)
            slots.append(i)
    empty = (np.empty(0, dtype=np.int64), np.empty(0))
    out = [empty] * len(hits)
    if not starts:
        return out
    matrix = [matrix_world[r][c] for r in range(3) for c in range(4)]
    verts, dists, counts = graph.gather_many(starts, points, matrix, radius, _BRUSH_MAX_HOPS)
    verts = np.asarray(verts, dtype=np.int64)
    dists = np.asarray(dists, dtype=np.float64)
    bounds = np.concatenate(([0], np.cumsum(counts))).tolist()
    for k, i in enumerate(slots):
        out[i] = (verts[bounds[k]:bounds[k + 1]], dists[bounds[k]:bounds[k + 1]])
    return out


def gather_brush_vertices(core_facade, bm, face_index, hit_location_world, matrix_world, radius,
                          posed_coords, graph=None):
    start_v = _walk_start(bm, face_index, hit_location_world, matrix_world, posed_coords)
    if start_v is None:
        return {}

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


def build_world_points(matrix_world, posed_coords, hidden_vertices=None):
    m = np.array(matrix_world, dtype=np.float64)
    co = posed_array(posed_coords)
    if hidden_vertices is not None:
        ids = np.flatnonzero(~np.asarray(hidden_vertices, dtype=bool))
    else:
        ids = np.arange(len(co))
    return ids, co[ids] @ m[:3, :3].T + m[:3, 3]


def gather_brush_vertices_volume(world_points, center_world, radius):
    if radius <= 1e-9:
        return {}
    ids, xyz = world_points
    center = np.asarray(center_world, dtype=np.float64)
    slab = np.flatnonzero(np.abs(xyz[:, 0] - center[0]) <= radius * (1.0 + 1e-9))
    d = np.linalg.norm(xyz[slab] - center, axis=1)
    inside = d <= radius
    return dict(zip(ids[slab[inside]].tolist(), d[inside].tolist()))


def keep_connected(gathered, seeds, adjacency, links=None):
    reached = {v for v in seeds if v in gathered}
    stack = list(reached)
    while stack:
        v = stack.pop()
        for n in chain(adjacency.get(v, ()), links.get(v, ()) if links else ()):
            if n in gathered and n not in reached:
                reached.add(n)
                stack.append(n)
    return {v: gathered[v] for v in reached}


def keep_connected_from(gathered, previous, adjacency, links=None):
    allowed = dict.fromkeys(previous, 0.0)
    allowed.update(gathered)
    reached = keep_connected(allowed, previous, adjacency, links)
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
    coords = posed_array(posed_coords).ravel()
    out_offsets, out_targets = core_facade.get_rust_gateway("layer_links").call(
        "rust_layer_links", coords, offsets, targets, float(max_dist), LAYER_LINK_MAX_EDGES,
    )
    links = VertexRows(out_offsets, out_targets)
    _links_cache = (key, adjacency, max_dist, links)
    return links


def build_screen_points(matrix_world, region, rv3d, posed_coords, hidden_vertices=None):
    persp = np.array(rv3d.perspective_matrix @ matrix_world, dtype=np.float64)
    co = posed_array(posed_coords)
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
    if isinstance(links, VertexRows) and len(links.offsets) == vert_count + 1:
        return links.offsets, links.values
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
    coords = np.ascontiguousarray(rest_array(rest_coords)).ravel()
    graph = core_facade.get_rust_gateway("brush_reach").call(
        "rust_reach_graph", coords, offsets, targets, link_offsets, link_targets, REACH_NEAR_EDGES,
    )
    _reach_cache = (key, adjacency, links, graph)
    return graph


class VertexRows(Mapping):
    """``{vertex: [values]}`` for the vertices with a row, over CSR arrays; a list is cut on ask."""

    def __init__(self, offsets, values):
        self.offsets = np.asarray(offsets, dtype=np.int64)
        self.values = np.asarray(values, dtype=np.int64)
        self._offsets = self.offsets.tolist()
        self._values = self.values.tolist()
        self._has = (np.diff(self.offsets) > 0).tobytes()

    def __contains__(self, v):
        return 0 <= v < len(self._has) and self._has[v] == 1

    def __getitem__(self, v):
        if v not in self:
            raise KeyError(v)
        return self._values[self._offsets[v]:self._offsets[v + 1]]

    def get(self, v, default=None):
        return self[v] if v in self else default

    def __iter__(self):
        return (v for v, has in enumerate(self._has) if has)

    def __len__(self):
        return self._has.count(1)


def brush_islands(gathered, adjacency, links=None, welds=None):
    left = set(gathered)
    islands = []
    while left:
        seed = left.pop()
        island = {seed}
        stack = [seed]
        while stack:
            v = stack.pop()
            for n in chain(adjacency.get(v, ()), links.get(v, ()) if links else (),
                           welds.get(v, ()) if welds else ()):
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
