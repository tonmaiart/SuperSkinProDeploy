
from collections import namedtuple

import numpy as np
from mathutils.kdtree import KDTree

from .mesh_flags import vertex_positions as _positions

INDEX_ATTR = ".ssp_orig"
_MAX_VERTICES = 1 << 24
_DISPLACED_STRETCH = 3.0
_DISPLACED_PASSES = 8
_FILL_NEIGHBOURS = 8
_ISOLATED_LIMIT = 2.0
_FIT_RIDGE = 0.05

DEFORM_ONLY_MODIFIERS = frozenset({
    'ARMATURE', 'CAST', 'CORRECTIVE_SMOOTH', 'CURVE', 'DATA_TRANSFER', 'DISPLACE',
    'HOOK', 'LAPLACIANDEFORM', 'LAPLACIANSMOOTH', 'LATTICE', 'MESH_DEFORM',
    'NORMAL_EDIT', 'SHRINKWRAP', 'SIMPLE_DEFORM', 'SMOOTH', 'SURFACE_DEFORM',
    'UV_PROJECT', 'UV_WARP', 'VERTEX_WEIGHT_EDIT', 'VERTEX_WEIGHT_MIX',
    'VERTEX_WEIGHT_PROXIMITY', 'WARP', 'WAVE', 'WEIGHTED_NORMAL',
})
_SMOOTHING_MODIFIERS = frozenset({'SUBSURF', 'MULTIRES'})
_MERGING_MODIFIERS = frozenset({'BEVEL', 'BOOLEAN', 'DECIMATE', 'NODES', 'WELD'})

Displayed = namedtuple("Displayed", "coords source first orig_of_eval")

_cache = {}


def _check_values(idx):
    idx = np.asarray(idx, dtype=np.int64)
    return ((((idx * 2654435761) % 1000003) + 1) / 1000004.0 + 0.5).astype(np.float32)


def _expected(count):
    data = np.empty((count, 2), dtype=np.float32)
    data[:, 0] = np.arange(count, dtype=np.float32)
    data[:, 1] = _check_values(np.arange(count))
    return data.ravel()


def ensure_index_map(mesh):
    count = len(mesh.vertices)
    attr = mesh.attributes.get(INDEX_ATTR)
    if count == 0 or count >= _MAX_VERTICES:
        remove_index_map(mesh)
        return False
    expected = _expected(count)
    if attr is not None:
        if attr.domain == 'POINT' and attr.data_type == 'FLOAT2':
            current = np.empty(count * 2, dtype=np.float32)
            attr.data.foreach_get("vector", current)
            if np.array_equal(current, expected):
                return True
        mesh.attributes.remove(attr)
    try:
        attr = mesh.attributes.new(INDEX_ATTR, 'FLOAT2', 'POINT')
    except (RuntimeError, TypeError, AttributeError):
        return False
    attr.data.foreach_set("vector", expected)
    _cache.pop(mesh.as_pointer(), None)
    return True


def remove_index_map(mesh):
    try:
        attr = mesh.attributes.get(INDEX_ATTR)
        if attr is not None:
            mesh.attributes.remove(attr)
    except (RuntimeError, ReferenceError, AttributeError):
        pass
    _cache.pop(mesh.as_pointer(), None)


def coarse_vertices_lead(obj):
    return all(m.type == 'SUBSURF' or m.type in DEFORM_ONLY_MODIFIERS
               for m in obj.modifiers if m.show_viewport)


def smooths_original_vertices(obj):
    return any(m.type in _SMOOTHING_MODIFIERS for m in obj.modifiers if m.show_viewport)


def _read_pairs(eval_mesh):
    attr = eval_mesh.attributes.get(INDEX_ATTR)
    if attr is None or attr.domain != 'POINT' or attr.data_type != 'FLOAT2':
        return None
    pairs = np.empty(len(eval_mesh.vertices) * 2, dtype=np.float32)
    attr.data.foreach_get("vector", pairs)
    return pairs.reshape(-1, 2)


def _orig_of_eval(pairs, count):
    x = pairs[:, 0]
    xi = x.astype(np.int64)
    exact = (x == xi) & (xi >= 0) & (xi < count)
    exact &= pairs[:, 1] == _check_values(np.where(exact, xi, 0))
    return np.where(exact, xi, -1)


def _first_copies(orig_of_eval, count):
    first = np.full(count, -1, dtype=np.int64)
    copies = np.nonzero(orig_of_eval >= 0)[0]
    if len(copies):
        originals, at = np.unique(orig_of_eval[copies], return_index=True)
        first[originals] = copies[at]
    return first


def _edges(mesh):
    flat = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", flat)
    return flat.reshape(-1, 2)


def _drop_displaced(first, rest, eval_co, edges):
    count = len(first)
    first = first.copy()
    limit = None
    for _ in range(_DISPLACED_PASSES):
        mapped = first >= 0
        keep = mapped[edges[:, 0]] & mapped[edges[:, 1]]
        a, b = edges[keep, 0], edges[keep, 1]
        if len(a) == 0:
            break
        rest_len = np.linalg.norm(rest[a] - rest[b], axis=1)
        drawn_len = np.linalg.norm(eval_co[first[a]] - eval_co[first[b]], axis=1)
        measurable = rest_len > 1e-9
        ratio = np.zeros(len(a))
        ratio[measurable] = drawn_len[measurable] / rest_len[measurable]
        if limit is None:
            scale = float(np.median(ratio[measurable])) if measurable.any() else 1.0
            limit = _DISPLACED_STRETCH * max(scale, 1e-6)
        stretched = measurable & (ratio > limit)
        if not stretched.any():
            break
        bad = np.bincount(a[stretched], minlength=count) + np.bincount(b[stretched], minlength=count)
        total = np.bincount(a, minlength=count) + np.bincount(b, minlength=count)
        share = bad / np.maximum(total, 1)
        sa, sb = a[stretched], b[stretched]
        culprits = np.concatenate([sa[share[sa] >= share[sb]], sb[share[sb] >= share[sa]]])
        first[culprits] = -1
    return first


def _neighbours(rest, have, queries, skip_self=False):
    k = min(_FILL_NEIGHBOURS, len(have) - (1 if skip_self else 0))
    if len(queries) == 0 or k <= 0:
        return None
    tree = KDTree(len(have))
    for slot, i in enumerate(have):
        tree.insert(rest[i], slot)
    tree.balance()
    near = np.empty((len(queries), k), dtype=np.int64)
    dist = np.empty((len(queries), k), dtype=np.float64)
    for row, i in enumerate(queries):
        found = [(have[slot], d) for _co, slot, d in tree.find_n(rest[i], k + 1 if skip_self else k)
                 if not (skip_self and have[slot] == i)][:k]
        for col, (j, d) in enumerate(found):
            near[row, col], dist[row, col] = j, d
    weight = 1.0 / np.maximum(dist, 1e-9)
    weight /= weight.sum(axis=1, keepdims=True)
    return near, weight, (weight * dist).sum(axis=1)


def _fit_coefficients(rest, near, weight, at):
    offsets = rest[near].astype(np.float64) - at[:, None, :]
    design = np.concatenate([offsets, np.ones(offsets.shape[:2] + (1,))], axis=2)
    weighted = design * weight[:, :, None]
    gram = np.einsum('mki,mkj->mij', weighted, design)
    ridge = _FIT_RIDGE * np.maximum((weight * (offsets ** 2).sum(axis=2)).sum(axis=1), 1e-12)
    for axis in range(3):
        gram[:, axis, axis] += ridge
    unit = np.zeros(gram.shape[:2] + (1,))
    unit[:, 3, 0] = 1.0
    intercept = np.linalg.solve(gram, unit)[..., 0]
    return np.einsum('mi,mki->mk', intercept, weighted).astype(np.float32)


def _shift_at(shift, near, coefficients):
    return (coefficients[:, :, None] * shift[near]).sum(axis=1)


def _drop_isolated(first, rest, eval_co, edges):
    mapped = first >= 0
    keep = mapped[edges[:, 0]] & mapped[edges[:, 1]]
    degree = np.bincount(edges[keep].ravel(), minlength=len(first))
    queries = np.nonzero(mapped & (degree < 2))[0]
    have = np.nonzero(mapped)[0]
    found = _neighbours(rest, have, queries, skip_self=True)
    if found is None:
        return first
    near, weight, spacing = found
    shift = np.zeros_like(rest)
    shift[have] = eval_co[first[have]] - rest[have]
    coefficients = _fit_coefficients(rest, near, weight, rest[queries])
    predicted = rest[queries] + _shift_at(shift, near, coefficients)
    residual = np.linalg.norm(eval_co[first[queries]] - predicted, axis=1)
    outliers = queries[residual > _ISOLATED_LIMIT * np.maximum(spacing, 1e-9)]
    if len(outliers):
        first = first.copy()
        first[outliers] = -1
    return first


def _fill_plan(rest, mapped):
    missing = np.nonzero(~mapped)[0]
    found = _neighbours(rest, np.nonzero(mapped)[0], missing)
    if found is None:
        return missing, None, None
    near, weight, _spacing = found
    return missing, near, _fit_coefficients(rest, near, weight, rest[missing])


def merges_vertices(obj):
    return any(m.type in _MERGING_MODIFIERS for m in obj.modifiers if m.show_viewport)


class _Mapping:
    __slots__ = ("shape", "first", "orig_of_eval", "held", "held_x", "held_check",
                 "missing", "near", "coefficients", "identity")


def _build_mapping(obj, pairs, eval_co, rest, shape):
    count = len(rest)
    m = _Mapping()
    m.shape = shape
    m.orig_of_eval = _orig_of_eval(pairs, count)
    first = _first_copies(m.orig_of_eval, count)
    if merges_vertices(obj):
        edges = _edges(obj.data)
        first = _drop_displaced(first, rest, eval_co, edges)
        first = _drop_isolated(first, rest, eval_co, edges)
    m.first = first
    m.held = np.nonzero(first >= 0)[0]
    m.held_x = m.held.astype(np.float32)
    m.held_check = _check_values(m.held)
    m.missing, m.near, m.coefficients = _fill_plan(rest, first >= 0)
    m.identity = shape[1] == count and np.array_equal(first, np.arange(count))
    return m


def _mapping(obj, eval_mesh, eval_co, rest_of):
    pairs = _read_pairs(eval_mesh)
    if pairs is None:
        return None
    key = obj.data.as_pointer()
    shape = (len(obj.data.vertices), len(eval_mesh.vertices), len(eval_mesh.polygons), len(eval_mesh.loops))
    cached = _cache.get(key)
    if cached is not None and cached.shape == shape:
        drawn = cached.first[cached.held]
        if (np.array_equal(pairs[drawn, 0], cached.held_x)
                and np.array_equal(pairs[drawn, 1], cached.held_check)):
            return cached
    mapping = _build_mapping(obj, pairs, eval_co, rest_of(), shape)
    _cache[key] = mapping
    return mapping


def displayed(obj, depsgraph):
    view = _evaluated_view(obj, depsgraph)
    from .session_display import stroke_positions
    live = stroke_positions(obj)
    if live is None or view.coords is None or len(view.coords) != len(live):
        return view
    return Displayed(np.array(live, dtype=np.float32), view.source, view.first, view.orig_of_eval)


def _evaluated_view(obj, depsgraph):
    mesh = obj.data
    count = len(mesh.vertices)
    try:
        eval_mesh = obj.evaluated_get(depsgraph).data
    except (RuntimeError, ReferenceError, AttributeError):
        return Displayed(_positions(mesh), 'rest', None, None)
    total = len(eval_mesh.vertices)
    eval_co = _positions(eval_mesh)
    rest_cache = []

    def rest_of():
        if not rest_cache:
            rest_cache.append(_positions(mesh))
        return rest_cache[0]

    mapping = _mapping(obj, eval_mesh, eval_co, rest_of) if count else None
    if mapping is not None and mapping.identity:
        return Displayed(eval_co, 'map', mapping.first, mapping.orig_of_eval)
    rest = rest_of()
    if mapping is not None and len(mapping.held):
        coords = rest.copy()
        coords[mapping.held] = eval_co[mapping.first[mapping.held]]
        if len(mapping.missing) and mapping.near is not None:
            shift = _shift_at(coords - rest, mapping.near, mapping.coefficients)
            coords[mapping.missing] = rest[mapping.missing] + shift
        return Displayed(coords, 'map', mapping.first, mapping.orig_of_eval)
    if total == count:
        identity = np.arange(count, dtype=np.int64)
        return Displayed(eval_co, 'direct', identity, identity)
    if total > count and coarse_vertices_lead(obj):
        first = np.arange(count, dtype=np.int64)
        orig_of_eval = np.full(total, -1, dtype=np.int64)
        orig_of_eval[:count] = first
        return Displayed(eval_co[:count].copy(), 'prefix', first, orig_of_eval)
    return Displayed(rest, 'rest', None, None)


def displayed_normals(obj, depsgraph, view):
    if view.first is None or (view.first < 0).any():
        return None
    eval_mesh = obj.evaluated_get(depsgraph).data
    normals = np.empty(len(eval_mesh.vertices) * 3, dtype=np.float32)
    eval_mesh.vertex_normals.foreach_get("vector", normals)
    return normals.reshape(-1, 3)[view.first]
