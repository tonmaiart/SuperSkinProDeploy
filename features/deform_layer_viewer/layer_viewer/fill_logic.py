
import heapq

import numpy as np

from ...weight_toolkit.public_api import block_weights, bone_data_for

DEFAULT_SMOOTH = 1.0
DEFAULT_REPEAT = 10

_PASSES_PER_REPEAT = 10
_MIN_EDGE = 1e-6
_PRUNE = 1e-3


def _normalized(row):
    total = sum(w for w in row.values() if w > 0.0)
    return {b: w / total for b, w in row.items() if w > 0.0} if total > 0.0 else {}


def _edge_graph(core_facade, num_verts):
    mesh = core_facade.get_mesh()
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    mat = np.array(core_facade.get_obj().matrix_world, dtype=np.float64)
    co = co @ mat[:3, :3].T + mat[:3, 3]
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2).astype(np.int64)
    edges = edges[(edges < num_verts).all(axis=1)]
    length = np.maximum(np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1), _MIN_EDGE)
    src = np.concatenate([edges[:, 0], edges[:, 1]])
    dst = np.concatenate([edges[:, 1], edges[:, 0]])
    order = np.argsort(src, kind="stable")
    return co[:num_verts], src[order], dst[order], 1.0 / np.concatenate([length, length])[order]


def _components(mask, src, dst):
    parent = np.arange(len(mask))
    keep = mask[src] & mask[dst] & (src < dst)
    a_l, b_l = src[keep].tolist(), dst[keep].tolist()
    par = parent.tolist()

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    for a, b in zip(a_l, b_l):
        ra, rb = find(a), find(b)
        if ra != rb:
            par[ra] = rb
    labels = np.array([find(v) for v in range(len(mask))], dtype=np.int64)
    labels[~mask] = -1
    return labels


def _anchor_verts(core_facade, co, bones):
    anchors = {}
    for name, head, tail in bone_data_for(core_facade.get_obj(), bones):
        h, t = np.asarray(head), np.asarray(tail)
        seg = t - h
        denom = float(seg @ seg)
        u = np.clip(((co - h) @ seg) / denom, 0.0, 1.0) if denom > 0.0 else np.zeros(len(co))
        anchors[name] = int(np.argmin(np.linalg.norm(co - (h + u[:, None] * seg), axis=1)))
    return anchors


def _accepted_block_regions(core_facade, co, free, weights, bones, src, dst, repeat):
    hole = np.flatnonzero(free).tolist()
    blocked = block_weights(core_facade, set(bones), hole, 0.0, repeat)
    col = {b: i for i, b in enumerate(bones)}
    pick = np.full(len(free), -1, dtype=np.int64)
    for v, row in blocked.items():
        if row:
            pick[v] = col[max(row, key=row.get)]
    accepted = np.zeros(len(free), dtype=bool)
    anchors = _anchor_verts(core_facade, co, bones)
    for c, bone in enumerate(bones):
        region = pick == c
        if not region.any():
            continue
        labels = _components(region, src, dst)
        good = set()
        touch = region[src] & ~free[dst] & (weights[dst, c] > 0.0)
        good.update(labels[src[touch]].tolist())
        anchor = anchors.get(bone)
        if anchor is not None and region[anchor]:
            good.add(int(labels[anchor]))
        if good:
            accepted |= region & np.isin(labels, list(good))
    return {v: bones[pick[v]] for v in np.flatnonzero(accepted).tolist()}


def _nearest_seed(num_verts, src, dst, inv_len, seeds):
    ptr = np.searchsorted(src, np.arange(num_verts + 1)).tolist()
    dst_l = dst.tolist()
    len_l = (1.0 / inv_len).tolist()
    dist = [float("inf")] * num_verts
    owner = [-1] * num_verts
    heap = []
    for s in seeds:
        dist[s] = 0.0
        owner[s] = s
        heap.append((0.0, s))
    heapq.heapify(heap)
    pop, push = heapq.heappop, heapq.heappush
    while heap:
        d, v = pop(heap)
        if d > dist[v]:
            continue
        ov = owner[v]
        for k in range(ptr[v], ptr[v + 1]):
            u = dst_l[k]
            nd = d + len_l[k]
            if nd < dist[u]:
                dist[u] = nd
                owner[u] = ov
                push(heap, (nd, u))
    return np.asarray(owner, dtype=np.int64)


def _relax(weights, free, src, dst, inv_len, passes):
    keep = free[src]
    src, dst, inv_len = src[keep], dst[keep], inv_len[keep]
    if not len(src):
        return
    starts = np.flatnonzero(np.r_[True, src[1:] != src[:-1]])
    rows = src[starts]
    wsum = np.add.reduceat(inv_len, starts)[:, None]
    scaled = inv_len[:, None]
    for _ in range(passes):
        weights[rows] = np.add.reduceat(weights[dst] * scaled, starts, axis=0) / wsum


def fill_transparent(core_facade, layer, num_verts, smooth=DEFAULT_SMOOTH, repeat=DEFAULT_REPEAT, hybrid=False):
    seeds = {}
    for v, row in layer.items():
        norm = _normalized(row)
        if norm and 0 <= v < num_verts:
            seeds[v] = norm
    if not seeds:
        return {}
    bones = sorted({b for row in seeds.values() for b in row})
    col = {b: i for i, b in enumerate(bones)}
    weights = np.zeros((num_verts, len(bones)), dtype=np.float64)
    for v, row in seeds.items():
        for b, w in row.items():
            weights[v, col[b]] = w

    co, src, dst, inv_len = _edge_graph(core_facade, num_verts)
    owner = _nearest_seed(num_verts, src, dst, inv_len, list(seeds))
    free = owner >= 0
    free[list(seeds)] = False
    if hybrid and free.any():
        placed = _accepted_block_regions(core_facade, co, free, weights, bones, src, dst, repeat)
        for v, b in placed.items():
            weights[v, col[b]] = 1.0
        owner = _nearest_seed(num_verts, src, dst, inv_len, list(seeds) + list(placed))
    weights[free] = weights[owner[free]]
    _relax(weights, free, src, dst, inv_len, max(0, round(smooth * repeat * _PASSES_PER_REPEAT)))

    result = {}
    for v in np.flatnonzero(free).tolist():
        row = weights[v]
        result[v] = _normalized({bones[i]: float(row[i]) for i in np.flatnonzero(row > _PRUNE).tolist()})

    unreached = [v for v in range(num_verts) if owner[v] < 0]
    if unreached:
        result.update(block_weights(core_facade, set(bones), unreached, smooth, repeat))
    return {v: row for v, row in result.items() if row}
