
from collections.abc import Mapping

import numpy as np

from .brush_surface import (
    LazyVertexNormals, MeshTopology, PosedCoords, SurfaceBVH, _NEAREST_REFINE, _NEAREST_SAMPLES,
    _VisibleFaceBVH, _coord_rows, _polygon_vertex_lists, _rest_memo, _spans, _surface_bvh,
    _surface_triangles, _vertex_coord_tuples, _vertex_normals, build_bvh, build_visible_bvh,
    drop_surface_cache, faces_view, facing_mask, get_posed_vertex_coordinates, keep_facing,
    mesh_topology, nearest_on_ray, polygon_hide_flags, posed_array, posed_vertex_normals,
    raycast_at, raycast_under_cursor, rest_array, rest_key, rest_positions, stroke_side_mask,
    vertex_hide_flags, welded_normals,
)
from .brush_gather import (
    LAYER_LINK_MAX_EDGES, REACH_MAX_DETOUR, REACH_NEAR_EDGES, SCREEN_RADIUS_PX_SCALE, VertexRows,
    _BRUSH_MAX_HOPS, _adjacency_csr, _bfs_within_world_radius, _links_csr, _walk_start,
    brush_islands, build_brush_graph, build_reach_graph, build_screen_points, build_world_points,
    gather_brush_vertices, gather_brush_vertices_screen, gather_brush_vertices_volume, gather_many,
    keep_connected, keep_connected_from, keep_reachable, layer_links, screen_mode_radius_px,
)
from .brush_welds import (
    _WELD_EDGE_SHARE, _closer_piece, _edge_csr, _median_edge, _nearer_than, _piece_links,
    _vertex_pieces, coincident_groups, coincident_welds, open_edges, weld_joined_pieces,
    weld_links,
)
from .brush_carry import (
    SEAM_CLOSED_TOL, _BIND_MAX_HOPS, _add_scaled, _blend, _keep_locks, _lerp, _rows_match, _shift,
    carry_bound_layers, joined_region, lead_rows, seam_pin_rows,
)


class LayerBind(Mapping):
    """Every bound vertex with the triangle corners and weights of the point it is bound to, as
    arrays. As a mapping it answers ``{corner vertex: [(bound vertex, corners, weights), ...]}``,
    each corner's list built on first ask."""

    def __init__(self, verts, corners, weights):
        self.verts = np.asarray(verts, dtype=np.int64)
        self.corners = np.asarray(corners, dtype=np.int64).reshape(-1, 3)
        self.weights = np.asarray(weights, dtype=np.float64).reshape(-1, 3)
        self._index = None
        self._rows = {}
        self._entries = None
        self._rest_gaps = None
        self._without = None
        self._plus = None

    @classmethod
    def of(cls, bind):
        global _bind_of_cache
        if isinstance(bind, cls):
            return bind
        cached = _bind_of_cache
        if cached is not None and cached[0] is bind:
            return cached[1]
        entries = {}
        for row in bind.values():
            for v, corners, weights in row:
                entries[v] = (corners, weights)
        made = cls(list(entries), [e[0] for e in entries.values()], [e[1] for e in entries.values()])
        _bind_of_cache = (bind, made)
        return made

    def _corner_index(self):
        if self._index is None:
            corners = self.corners
            count = len(corners)
            first = np.ones((count, 3), dtype=np.bool_)
            first[:, 1] = corners[:, 1] != corners[:, 0]
            first[:, 2] = (corners[:, 2] != corners[:, 0]) & (corners[:, 2] != corners[:, 1])
            listed = corners[first]
            entry = np.repeat(np.arange(count, dtype=np.int64), 3).reshape(count, 3)[first]
            order = np.argsort(listed, kind="stable")
            top = int(listed.max()) + 1 if len(listed) else 0
            offsets = np.concatenate(([0], np.cumsum(np.bincount(listed, minlength=top))))
            self._index = (offsets, entry[order])
        return self._index

    def get(self, corner, default=()):
        row = self._rows.get(corner)
        if row is None:
            offsets, entry = self._corner_index()
            row = ()
            if 0 <= corner < len(offsets) - 1:
                at = entry[offsets[corner]:offsets[corner + 1]]
                if len(at):
                    row = list(zip(self.verts[at].tolist(), map(tuple, self.corners[at].tolist()),
                                   map(tuple, self.weights[at].tolist())))
            self._rows[corner] = row
        return row or default

    def __getitem__(self, corner):
        row = self.get(corner, None)
        if row is None:
            raise KeyError(corner)
        return row

    def __iter__(self):
        return iter(np.flatnonzero(np.diff(self._corner_index()[0])).tolist())

    def __len__(self):
        return int(np.count_nonzero(np.diff(self._corner_index()[0])))

    def entries(self):
        if self._entries is None:
            self._entries = dict(zip(self.verts.tolist(), zip(map(tuple, self.corners.tolist()),
                                                              map(tuple, self.weights.tolist()))))
        return self._entries

    def gaps(self, points):
        bound = (self.weights[:, :, None] * points[self.corners]).sum(axis=1)
        return np.linalg.norm(points[self.verts] - bound, axis=1)

    def rest_gaps(self, rest_coords):
        key = rest_key(rest_coords)
        if self._rest_gaps is None or self._rest_gaps[0] != key:
            self._rest_gaps = (key, self.gaps(rest_array(rest_coords)))
        return self._rest_gaps[1]

    def without(self, dropped):
        key = dropped.tobytes()
        if self._without is None or self._without[0] != key:
            keep = ~dropped
            self._without = (key, LayerBind(self.verts[keep], self.corners[keep], self.weights[keep]))
        return self._without[1]

    def plus(self, other):
        if self._plus is None or self._plus[0] is not other:
            new = ~np.isin(other.verts, self.verts)
            made = self
            if new.any():
                made = LayerBind(np.concatenate((self.verts, other.verts[new])),
                                 np.concatenate((self.corners, other.corners[new])),
                                 np.concatenate((self.weights, other.weights[new])))
            self._plus = (other, made)
        return self._plus[1]


_bind_of_cache = None
_bind_cache = None


def build_layer_bind(core_facade, rest_coords, mesh):
    global _bind_cache
    adjacency = core_facade.get_cached_mesh_neighbors()
    key = rest_key(rest_coords)
    cached = _bind_cache
    if cached is not None and cached[0] == key and cached[1] is adjacency:
        return cached[2]
    bind = _layer_bind(core_facade, rest_coords, mesh, adjacency)
    _bind_cache = (key, adjacency, bind)
    return bind


def _layer_bind(core_facade, rest_coords, mesh, adjacency):
    from ....core.facade import CoreFacade
    tris, _faces = mesh_topology(mesh).triangles(mesh)
    points = np.ascontiguousarray(posed_array(rest_coords))
    corners, weights = core_facade.get_rust_gateway("layer_bind").call(
        "rust_layer_bind", points.ravel(), np.ascontiguousarray(tris, dtype=np.int64),
    )
    corners = np.asarray(corners, dtype=np.int64).reshape(-1, 3)
    weights = np.asarray(weights, dtype=np.float64).reshape(-1, 3)
    count = len(corners)
    valid = corners[:, 0] >= 0
    safe = np.where(valid[:, None], corners, 0)
    on = (weights > CoreFacade.WEIGHT_FLOOR) & valid[:, None]
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
    keep = keep[~_closer_piece(points, keep, gap[keep], _vertex_pieces(mesh), median,
                               lambda reach: _piece_links(core_facade, mesh, points, reach))]
    return LayerBind(keep, corners[keep], weights[keep])


_BIND_DRIFT_EDGES = 4.0


def drop_drifted_binds(core_facade, bind, rest_coords, posed_coords):
    arrays = LayerBind.of(bind)
    if not len(arrays.verts) or posed_coords is None:
        return bind
    rest = rest_array(rest_coords)
    posed = posed_array(posed_coords)
    if len(posed) != len(rest):
        return bind
    median = _median_edge(rest, core_facade.get_cached_mesh_neighbors())
    drifted = arrays.gaps(posed) - arrays.rest_gaps(rest_coords) > _BIND_DRIFT_EDGES * median
    if median <= 0.0 or not drifted.any():
        return bind
    return arrays.without(drifted)


_bind_pose_cache = None


def _bone_pose_key(obj):
    parts = []
    for md in obj.modifiers:
        rig = getattr(md, "object", None)
        if md.type != 'ARMATURE' or rig is None or rig.type != 'ARMATURE':
            continue
        matrices = np.empty(len(rig.pose.bones) * 16, dtype=np.float32)
        rig.pose.bones.foreach_get("matrix", matrices)
        parts += [rig.name.encode(), np.asarray(rig.matrix_world, dtype=np.float32).tobytes(),
                  matrices.tobytes()]
    if not parts:
        return None
    return b"".join(parts) + np.asarray(obj.matrix_world, dtype=np.float32).tobytes()


def bind_pose(obj, rest_coords, posed_coords):
    global _bind_pose_cache
    key = None if obj is None or posed_coords is None else _bone_pose_key(obj)
    if key is None:
        return posed_coords
    full = (obj.session_uid, rest_key(rest_coords), key)
    cached = _bind_pose_cache
    if cached is not None and cached[0] == full:
        return cached[1]
    frozen = posed_array(posed_coords).copy()
    _bind_pose_cache = (full, frozen)
    return frozen


def stroke_layer_bind(core_facade, rest_coords, posed_coords, mesh):
    obj = core_facade.get_obj()
    posed_coords = bind_pose(obj, rest_coords, posed_coords)
    rest_bind = build_layer_bind(core_facade, rest_coords, mesh)
    return drop_drifted_binds(core_facade, rest_bind, rest_coords, posed_coords)


def keep_volume_steps(core_facade, rest_coords, posed_coords, mesh, local_radius):
    bind = stroke_layer_bind(core_facade, rest_coords, posed_coords, mesh)
    yield
    welds = coincident_welds(core_facade, rest_coords) or None
    links = layer_links(core_facade, rest_coords, local_radius) or None
    yield
    graph = build_reach_graph(core_facade, rest_coords, links)
    yield
    piece_of, _root = weld_joined_pieces(graph.piece_of, welds, len(rest_coords))
    layer_depths(bind, piece_of, rest_coords, mesh)
    yield
    smooth_links(core_facade, bind, rest_coords, mesh)


def piece_depths(bind, pieces, points, normals):
    arrays = LayerBind.of(bind)
    verts, corners, weights = arrays.verts, arrays.corners, arrays.weights
    if not len(verts):
        return {}
    own = pieces[verts]
    other = pieces[corners[np.arange(len(verts)), weights.argmax(axis=1)]]
    keep = own != other
    if not keep.any():
        return {}
    verts, corners, weights, own, other = verts[keep], corners[keep], weights[keep], own[keep], other[keep]
    bound = np.einsum("ij,ijk->ik", weights, points[corners])
    normal = np.einsum("ij,ijk->ik", weights, normals[corners])
    above = np.einsum("ij,ij->i", points[verts] - bound, normal) > 0.0
    inner = np.where(above, other, own)
    outer = np.where(above, own, other)
    stride = int(max(inner.max(), outer.max())) + 1
    pairs, counts = np.unique(inner * stride + outer, return_counts=True)
    votes = {divmod(int(pair), stride): int(n) for pair, n in zip(pairs, counts)}
    under = {}
    for (a, b), n in votes.items():
        margin = n - votes.get((b, a), 0)
        if margin > 0:
            under.setdefault(b, {})[a] = margin
            under.setdefault(a, {})
    depths, left, depth = {}, set(under), 0
    while left:
        ready = [q for q in left if not any(r in left for r in under[q])]
        if not ready:
            ready = [min(left, key=lambda q: (sum(m for r, m in under[q].items() if r in left), q))]
        for q in ready:
            depths[q] = depth
        left.difference_update(ready)
        depth += 1
    return depths


_depths_cache = None


def layer_depths(bind, piece_of, rest_coords, mesh):
    global _depths_cache
    key = rest_key(rest_coords)
    cached = _depths_cache
    if cached is not None and cached[0] is bind and cached[1] == key:
        return cached[2]
    count = len(mesh.vertices)
    normals = np.empty(count * 3, dtype=np.float32)
    mesh.vertex_normals.foreach_get("vector", normals)
    normals = normals.astype(np.float64)
    points = rest_array(rest_coords)
    pieces = np.fromiter(map(piece_of, range(count)), dtype=np.int64, count=count)
    depths = piece_depths(bind, pieces, points, normals.reshape(-1, 3))
    _depths_cache = (bind, key, depths)
    return depths


def inward_links(core_facade, links, selected, bind, welds, rest_coords, mesh):
    piece_of = weld_joined_pieces(build_reach_graph(core_facade, rest_coords).piece_of, welds,
                                  len(rest_coords))[0]
    depths = layer_depths(bind, piece_of, rest_coords, mesh)
    neighbours = core_facade.get_cached_mesh_neighbors()
    chosen = set(selected)
    depth = lambda v: depths.get(piece_of(v), 0)
    linked = lambda v: (links.get(v) if hasattr(links, "get") else (links[v] if v in links else None)) or ()
    out = {}
    for v in selected:
        partners = linked(v)
        if not len(partners):
            continue
        own = depth(v)
        near = neighbours.get(v, ()) if hasattr(neighbours, "get") else neighbours[v]
        patch = sum(1 for u in near if len(linked(u))) * 2 >= len(near)
        kept = [u for u in partners if u in chosen or (patch and depth(u) <= own)]
        if kept:
            out[v] = kept
    return out


def depth_leader(depths, pieces, prefer):
    return min(pieces, key=lambda q: (depths.get(q, 0), q != prefer, q))


_bind_links_cache = None


def bind_links(bind, rest_coords):
    global _bind_links_cache
    key = rest_key(rest_coords)
    cached = _bind_links_cache
    if cached is not None and cached[0] is bind and cached[1] == key:
        return cached[2]
    links = _pair_rows(*_bind_pairs(bind, rest_coords), len(rest_array(rest_coords)))
    _bind_links_cache = (bind, key, links)
    return links


def _bind_pairs(bind, rest_coords):
    arrays = LayerBind.of(bind)
    if not len(arrays.verts):
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    points = rest_array(rest_coords)
    verts = arrays.verts
    nearest = arrays.corners[np.arange(len(verts)), arrays.weights.argmax(axis=1)]
    keep = (nearest != verts) & (np.linalg.norm(points[verts] - points[nearest], axis=1) > 1e-9)
    return verts[keep], nearest[keep]


def _pair_rows(a, b, count):
    if not len(a):
        return {}
    pairs = np.unique(np.concatenate([a, b]) * count + np.concatenate([b, a]))
    heads, tails = np.divmod(pairs, count)
    return VertexRows(np.concatenate(([0], np.cumsum(np.bincount(heads, minlength=count)))), tails)


_shell_cache = None


def shell_pairs(core_facade, rest_coords, mesh):
    global _shell_cache
    adjacency = core_facade.get_cached_mesh_neighbors()
    key = rest_key(rest_coords)
    cached = _shell_cache
    if cached is not None and cached[0] == key and cached[1] is adjacency:
        return cached[2]
    tris, _faces = mesh_topology(mesh).triangles(mesh)
    points = np.ascontiguousarray(posed_array(rest_coords))
    partner = np.asarray(core_facade.get_rust_gateway("layer_bind").call(
        "rust_layer_shell", points.ravel(), np.ascontiguousarray(tris, dtype=np.int64),
    ), dtype=np.int64)
    verts = np.flatnonzero(partner >= 0)
    pairs = (verts, partner[verts])
    _shell_cache = (key, adjacency, pairs)
    return pairs


_smooth_links_cache = None


def smooth_links(core_facade, bind, rest_coords, mesh):
    global _smooth_links_cache
    shell = shell_pairs(core_facade, rest_coords, mesh)
    key = rest_key(rest_coords)
    cached = _smooth_links_cache
    if cached is not None and cached[0] is bind and cached[1] is shell and cached[2] == key:
        return cached[3]
    a, b = _bind_pairs(bind, rest_coords) if bind else (np.empty(0, dtype=np.int64),) * 2
    links = _pair_rows(np.concatenate([a, shell[0]]), np.concatenate([b, shell[1]]),
                       len(rest_array(rest_coords)))
    _smooth_links_cache = (bind, shell, key, links)
    return links
