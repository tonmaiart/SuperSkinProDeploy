
import numpy as np

MAX_RINGS = 8


def vertex_rings(n, edges, anchored):
    edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
    src = np.concatenate((edges[:, 0], edges[:, 1]))
    dst = np.concatenate((edges[:, 1], edges[:, 0]))
    ring = np.full(n, -1, dtype=np.int64)
    ring[np.asarray(anchored, dtype=bool)] = 0
    for r in range(1, MAX_RINGS + 1):
        hit = (ring[src] == r - 1) & (ring[dst] == -1)
        if not hit.any():
            break
        ring[dst[hit]] = r
    return ring, src, dst


def _earlier_pairs(ring, src, dst, r):
    keep = (ring[dst] == r) & (ring[src] >= 0) & (ring[src] < r)
    return dst[keep], src[keep]


def spread_values(ring, src, dst, values):
    out = np.asarray(values, dtype=np.float64).copy()
    for r in range(1, int(ring.max(initial=0)) + 1):
        v, u = _earlier_pairs(ring, src, dst, r)
        if not len(v):
            continue
        count = np.bincount(v, minlength=len(out))
        total = np.bincount(v, weights=out[u], minlength=len(out))
        at = np.flatnonzero(ring == r)
        out[at] = total[at] / np.maximum(count[at], 1)
    return out


def _csr(n, verts):
    order = np.argsort(verts, kind="stable")
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.add.at(indptr, verts + 1, 1)
    return order, np.cumsum(indptr)


def _gather(indptr, sources):
    starts = indptr[sources]
    counts = indptr[sources + 1] - starts
    pair = np.repeat(np.arange(len(sources)), counts)
    offset = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    return pair, starts[pair] + offset


def gather_rows(targets, source_of, n_sources, verts, keys, weights):
    verts = np.asarray(verts, dtype=np.int64)
    keys = np.asarray(keys, dtype=np.int64)
    weights = np.asarray(weights, dtype=np.float64)
    targets = np.asarray(targets, dtype=np.int64)
    order, indptr = _csr(n_sources, verts)
    pair, position = _gather(indptr, np.asarray(source_of, dtype=np.int64)[targets])
    entry = order[position]
    return targets[pair], keys[entry], weights[entry]


def spread_rows(ring, src, dst, verts, keys, weights):
    n = len(ring)
    verts = np.asarray(verts, dtype=np.int64)
    keys = np.asarray(keys, dtype=np.int64)
    weights = np.asarray(weights, dtype=np.float64)
    for r in range(1, int(ring.max(initial=0)) + 1):
        v, u = _earlier_pairs(ring, src, dst, r)
        if not len(v):
            continue
        order, indptr = _csr(n, verts)
        has = (indptr[u + 1] - indptr[u]) > 0
        v, u = v[has], u[has]
        if not len(v):
            continue
        holders = np.bincount(v, minlength=n)
        pair, position = _gather(indptr, u)
        entry = order[position]
        span = int(keys.max()) + 1
        cell, inverse = np.unique(v[pair] * span + keys[entry], return_inverse=True)
        summed = np.bincount(inverse, weights=weights[entry], minlength=len(cell))
        cell_v, cell_k = np.divmod(cell, span)
        verts = np.concatenate((verts, cell_v))
        keys = np.concatenate((keys, cell_k))
        weights = np.concatenate((weights, summed / holders[cell_v]))
    return verts, keys, weights
