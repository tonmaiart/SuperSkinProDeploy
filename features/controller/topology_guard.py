
import bpy
import numpy as np

from ...core.facade import CoreFacade
from ..weight_apply.public_api import vertex_positions
from .failure_report import report_failure

_GEN_ID_KEY = "ssp_native_guard_gen"
_VCOUNT_ID_KEY = "ssp_native_guard_vcount"
_POSITIONS_ID_KEY = "ssp_native_guard_positions"
_RETIRED_KEYS = ("ssp_native_guard_sig2", "ssp_native_guard_sig")
_WEIGHT_EPSILON = 1e-5


def _real_weight_snapshot(obj) -> list:
    managed = CoreFacade.managed_vg_names(obj)
    vg_names = {vg.index: vg.name for vg in obj.vertex_groups if vg.name in managed}
    if not vg_names:
        return []
    rows = []
    for v_idx, items in CoreFacade.iter_vertex_group_weights(obj.data):
        for group, weight in items:
            name = vg_names.get(group)
            if name is None:
                continue
            w = round(weight, 5)
            if w <= _WEIGHT_EPSILON:
                continue
            rows.append((v_idx, name, w))
    rows.sort()
    return rows


def _same_positions(stored, co) -> bool:
    try:
        return stored is not None and np.array_equal(np.frombuffer(stored, dtype=np.float64), co)
    except (TypeError, ValueError):
        return False


def record_baseline(obj) -> None:
    mesh = obj.data
    for key in _RETIRED_KEYS:
        if key in mesh:
            del mesh[key]
    mesh[_GEN_ID_KEY] = int(obj.get(CoreFacade.DEFORM_GEN_KEY, 0))
    mesh[_VCOUNT_ID_KEY] = len(mesh.vertices)
    co = vertex_positions(mesh).reshape(-1).astype(np.float64)
    if not _same_positions(mesh.get(_POSITIONS_ID_KEY), co):
        if _POSITIONS_ID_KEY in mesh:
            del mesh[_POSITIONS_ID_KEY]
        mesh[_POSITIONS_ID_KEY] = co


def _has_baseline(mesh) -> bool:
    return mesh.get(_VCOUNT_ID_KEY) is not None


def _written_since_record(obj) -> bool:
    return int(obj.get(CoreFacade.DEFORM_GEN_KEY, 0)) != int(obj.data.get(_GEN_ID_KEY, -1))


def is_baseline_current(obj) -> bool:
    mesh = obj.data
    return (_has_baseline(mesh) and not _written_since_record(obj)
            and int(mesh.get(_VCOUNT_ID_KEY, -1)) == len(mesh.vertices))


def needs_topology_resolve(obj) -> bool:
    if obj is None or obj.type != 'MESH' or not _has_baseline(obj.data):
        return False
    return not _written_since_record(obj) and vertex_count_changed(obj)


_pending_refresh: set = set()


def schedule_refresh_after_reflatten(obj) -> None:
    if (obj is None or obj.type != 'MESH' or obj.mode != 'OBJECT'
            or not _has_baseline(obj.data) or not _written_since_record(obj)
            or obj.name in _pending_refresh):
        return
    _pending_refresh.add(obj.name)
    bpy.app.timers.register(lambda name=obj.name: _refresh_deferred(name), first_interval=0.0)


def _refresh_deferred(name):
    _pending_refresh.discard(name)
    obj = bpy.data.objects.get(name)
    try:
        if (obj is not None and obj.type == 'MESH' and obj.mode == 'OBJECT'
                and not CoreFacade.in_session(obj)
                and not any(vg.name.startswith(CoreFacade.TEMP_VG_PREFIX) for vg in obj.vertex_groups)
                and _has_baseline(obj.data) and _written_since_record(obj)):
            record_baseline(obj)
    except Exception as exc:
        report_failure("core_pipeline", "topology_guard deferred record_baseline()", exc)
    return None


def vertex_count_changed(obj) -> bool:
    if obj is None or obj.type != 'MESH':
        return False
    stored = obj.data.get(_VCOUNT_ID_KEY)
    if stored is None:
        return False
    return len(obj.data.vertices) != stored


def import_native_into_active_layer(context, obj) -> None:
    layer_dict = {v: {} for v in range(len(obj.data.vertices))}
    for v_idx, name, w in _real_weight_snapshot(obj):
        layer_dict.setdefault(v_idx, {})[name] = w
    facade = CoreFacade(context)
    facade.write_layer_dict(layer_dict)
    facade.finish(color_only=False)


def _native_equals_single_layer(facade) -> bool:
    layers = facade.get_meta_list()
    if len(layers) != 1:
        return False
    layer = layers[0]
    return (bool(layer.get("visible", True))
            and float(layer.get("mask_default", 1.0)) == 1.0
            and not facade.get_active_mask_dict())


def resolve_topology_change(context, obj) -> None:
    if _native_equals_single_layer(CoreFacade(context)) or not remap_layers_by_position(context, obj):
        import_native_into_active_layer(context, obj)


def _nearest(tree_points, queries):
    from mathutils.kdtree import KDTree
    tree = KDTree(len(tree_points))
    for i, co in enumerate(tree_points):
        tree.insert(co, i)
    tree.balance()
    found = [tree.find(co)[1:] for co in queries]
    index = np.fromiter((f[0] for f in found), dtype=np.int64, count=len(found))
    dist = np.fromiter((f[1] for f in found), dtype=np.float64, count=len(found))
    return index, dist


def _anchors(nearest_old, dist, n):
    order = np.lexsort((dist, nearest_old))
    first = np.ones(len(order), dtype=bool)
    first[1:] = nearest_old[order][1:] != nearest_old[order][:-1]
    anchored = np.zeros(n, dtype=bool)
    anchored[order[first]] = True
    return anchored


def _mesh_edges(mesh):
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", edges)
    return edges.reshape(-1, 2)


def remap_layers_by_position(context, obj) -> bool:
    old_flat = obj.data.get(_POSITIONS_ID_KEY)
    old_num = len(old_flat) // 3 if old_flat else 0
    if old_num == 0:
        return False
    from . import topology_interp

    mesh = obj.data
    old_co = np.asarray(old_flat, dtype=np.float64).reshape(-1, 3)
    new_co = vertex_positions(mesh).reshape(-1, 3).astype(np.float64)
    n = len(new_co)
    nearest_old, dist = _nearest(old_co, new_co)
    anchored = _anchors(nearest_old, dist, n)
    ring, src, dst = topology_interp.vertex_rings(n, _mesh_edges(mesh), anchored)
    fixed = ring <= 0

    facade = CoreFacade(context)
    saved_active = facade.get_active_layer_index()
    for layer in facade.get_meta_list():
        facade.switch_to_layer(int(layer["index"]))
        old_layer = facade.get_active_layer_dict()
        old_mask = facade.get_active_mask_dict()
        mask_default = float(layer.get("mask_default", 1.0))

        names, key_of = [], {}
        old_v, old_k, old_w = [], [], []
        for o, row in old_layer.items():
            if not 0 <= o < old_num:
                continue
            for name, w in row.items():
                if name not in key_of:
                    key_of[name] = len(names)
                    names.append(name)
                old_v.append(o)
                old_k.append(key_of[name])
                old_w.append(w)
        verts, keys, weights = topology_interp.gather_rows(
            np.flatnonzero(fixed), nearest_old, old_num, old_v, old_k, old_w)
        verts, keys, weights = topology_interp.spread_rows(ring, src, dst, verts, keys, weights)

        new_layer = {v: {} for v in range(n)}
        for v, k, w in zip(verts.tolist(), keys.tolist(), weights.tolist()):
            new_layer[v][names[k]] = w

        old_mask_values = np.full(old_num, mask_default)
        for o, m in old_mask.items():
            if 0 <= o < old_num:
                old_mask_values[o] = m
        mask = np.where(fixed, old_mask_values[nearest_old], 0.0)
        mask = topology_interp.spread_values(ring, src, dst, mask)
        new_mask = {v: m for v, m in enumerate(mask.tolist()) if m != mask_default}

        facade.write_layer_dict(new_layer)
        facade.write_mask_dict(new_mask)

    facade.switch_to_layer(saved_active)
    facade.finish(color_only=False)
    return True
