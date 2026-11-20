
import numpy as np

from mathutils.kdtree import KDTree

from .. import mesh_flags
from .brush_surface import rest_key
from .brush_gather import LAYER_LINK_MAX_EDGES

_weld_cache = None
_WELD_EDGE_SHARE = 0.05


def _median_edge(coords, adjacency):
    ends = [(v, u) for v in range(0, len(coords), max(1, len(coords) // 2000)) for u in adjacency.get(v, ())]
    if not ends:
        return 0.0
    ends = np.asarray(ends, dtype=np.int64)
    return float(np.median(np.linalg.norm(coords[ends[:, 0]] - coords[ends[:, 1]], axis=1)))


def open_edges(mesh):
    loop_edges = np.empty(len(mesh.loops), dtype=np.intc)
    corner_edge = mesh.attributes.get(".corner_edge")
    if corner_edge is not None and corner_edge.data_type == 'INT' and corner_edge.domain == 'CORNER':
        corner_edge.data.foreach_get("value", loop_edges)
    else:
        mesh.loops.foreach_get("edge_index", loop_edges)
    faces = np.bincount(loop_edges, minlength=len(mesh.edges))
    pairs = np.sort(mesh_flags.edge_vertices(mesh).astype(np.int64)[faces == 1], axis=1)
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


_weld_links_cache = None


def weld_links(welds):
    global _weld_links_cache
    cached = _weld_links_cache
    if cached is not None and cached[0] is welds:
        return cached[1]
    groups = {}
    for v, r in (welds or {}).items():
        groups.setdefault(r, set()).update((v, r))
    links = {v: [u for u in group if u != v] for group in groups.values() for v in group}
    _weld_links_cache = (welds, links)
    return links


_joined_cache = None


def weld_joined_pieces(piece_of, welds, count):
    global _joined_cache
    owner = getattr(piece_of, "__self__", piece_of)
    cached = _joined_cache
    if cached is not None and cached[0] is owner and cached[1] is welds and cached[2] == count:
        return cached[3]
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
    joined = (pieces.__getitem__, root)
    _joined_cache = (owner, welds, count, joined)
    return joined


def _vertex_pieces(mesh):
    ends = mesh_flags.edge_vertices(mesh).astype(np.int64)
    a, b = ends[:, 0], ends[:, 1]
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


def _edge_csr(mesh):
    ends = mesh_flags.edge_vertices(mesh).astype(np.int64)
    heads = np.concatenate((ends[:, 0], ends[:, 1]))
    tails = np.concatenate((ends[:, 1], ends[:, 0]))
    order = np.argsort(heads, kind="stable")
    offsets = np.concatenate(([0], np.cumsum(np.bincount(heads, minlength=len(mesh.vertices)))))
    return offsets.astype(np.int64), tails[order]


def _nearer_than(points, pieces, verts, reach):
    tree = KDTree(len(points))
    for i, co in enumerate(points.tolist()):
        tree.insert(co, i)
    tree.balance()
    out = np.zeros(len(verts), dtype=np.bool_)
    for k, (v, r) in enumerate(zip(verts.tolist(), reach.tolist())):
        own = pieces[v]
        out[k] = any(pieces[i] != own for _co, i, _d in tree.find_range(points[v], r))
    return out


def _piece_links(core_facade, mesh, points, reach):
    offsets, targets = _edge_csr(mesh)
    heads = np.repeat(np.arange(len(points)), np.diff(offsets))
    once = targets > heads
    lengths = np.linalg.norm(points[heads[once]] - points[targets[once]], axis=1)
    if not len(lengths):
        return 0.0, None, None
    limit = (LAYER_LINK_MAX_EDGES - 0.1) * float(np.partition(lengths, len(lengths) // 2)[len(lengths) // 2])
    link_offsets, link_targets = core_facade.get_rust_gateway("layer_links").call(
        "rust_layer_links", np.ascontiguousarray(points).ravel(), offsets, targets,
        min(float(reach), limit) * (1.0 + 1e-9), LAYER_LINK_MAX_EDGES,
    )
    return limit, np.asarray(link_offsets, dtype=np.int64), np.asarray(link_targets, dtype=np.int64)


def _closer_piece(points, verts, dist, pieces, median, piece_links=None):
    out = np.zeros(len(verts), dtype=np.bool_)
    check = np.flatnonzero(dist > median)
    if median <= 0.0 or not len(check):
        return out
    asked = verts[check]
    reach = dist[check] - median
    covered = np.zeros(len(check), dtype=np.bool_)
    if piece_links is not None:
        limit, link_offsets, link_targets = piece_links(float(reach.max()))
        covered = reach < limit
    if covered.any():
        mine = asked[covered]
        counts = link_offsets[mine + 1] - link_offsets[mine]
        starts = np.cumsum(counts) - counts
        picked = np.repeat(link_offsets[mine] - starts, counts) + np.arange(int(counts.sum()), dtype=np.int64)
        owner = np.repeat(np.arange(len(mine)), counts)
        gap = np.linalg.norm(points[mine[owner]] - points[link_targets[picked]], axis=1)
        near = gap <= np.repeat(reach[covered], counts)
        out[check[covered]] = np.bincount(owner[near], minlength=len(mine)) > 0
    if not covered.all():
        far = ~covered
        out[check[far]] = _nearer_than(points, pieces, asked[far], reach[far])
    return out
