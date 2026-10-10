
import hashlib

import numpy as np

from ...core.facade import CoreFacade

_SIGNATURE_ID_KEY = "ssp_native_guard_sig"
_VCOUNT_ID_KEY = "ssp_native_guard_vcount"
_POSITIONS_ID_KEY = "ssp_native_guard_positions"
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


def compute_deform_signature(obj) -> str:
    managed = CoreFacade.managed_vg_names(obj)
    names = {vg.index: vg.name for vg in obj.vertex_groups if vg.name in managed}
    digest = hashlib.sha256()
    if not names:
        return digest.hexdigest()
    vids, gids, weights = [], [], []
    for v_idx, items in CoreFacade.iter_vertex_group_weights(obj.data):
        for group, weight in items:
            vids.append(v_idx)
            gids.append(group)
            weights.append(weight)
    sorted_names = sorted(names.values())
    rank_of = {name: rank for rank, name in enumerate(sorted_names)}
    lut = np.full(max(max(names), max(gids, default=0)) + 1, -1, dtype=np.int64)
    for gid, name in names.items():
        lut[gid] = rank_of[name]
    vids = np.asarray(vids, dtype=np.int64)
    ranks = lut[np.asarray(gids, dtype=np.int64)]
    quantized = np.rint(np.asarray(weights, dtype=np.float64) / _WEIGHT_EPSILON).astype(np.int64)
    keep = (ranks >= 0) & (quantized > 1)
    vids, ranks, quantized = vids[keep], ranks[keep], quantized[keep]
    order = np.lexsort((ranks, vids))
    digest.update("\0".join(sorted_names).encode("utf-8"))
    for column in (vids, ranks, quantized):
        digest.update(column[order].tobytes())
    return digest.hexdigest()


def record_flatten_signature(obj) -> None:
    mesh = obj.data
    mesh[_SIGNATURE_ID_KEY] = compute_deform_signature(obj)
    mesh[_VCOUNT_ID_KEY] = len(mesh.vertices)
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    mesh[_POSITIONS_ID_KEY] = co.tolist()


def has_native_mismatch(obj) -> bool:
    if obj is None or obj.type != 'MESH':
        return False
    stored = obj.data.get(_SIGNATURE_ID_KEY)
    if not stored:
        return False
    return stored != compute_deform_signature(obj)


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


def remap_layers_by_position(context, obj) -> bool:
    old_flat = obj.data.get(_POSITIONS_ID_KEY)
    old_num = len(old_flat) // 3 if old_flat else 0
    if old_num == 0:
        return False

    from mathutils import Vector
    from mathutils.kdtree import KDTree

    tree = KDTree(old_num)
    for i in range(old_num):
        tree.insert(Vector(old_flat[3 * i:3 * i + 3]), i)
    tree.balance()

    remap = {new_i: tree.find(v.co)[1] for new_i, v in enumerate(obj.data.vertices)}

    facade = CoreFacade(context)
    saved_active = facade.get_active_layer_index()
    for layer in facade.get_meta_list():
        facade.switch_to_layer(int(layer["index"]))
        old_layer = facade.get_active_layer_dict()
        old_mask = facade.get_active_mask_dict()
        mask_default = float(layer.get("mask_default", 1.0))

        new_layer = {n: old_layer[o] for n, o in remap.items() if o in old_layer}
        new_mask = {}
        for n, o in remap.items():
            v = old_mask.get(o, mask_default)
            if v != mask_default:
                new_mask[n] = v

        facade.write_layer_dict(new_layer)
        facade.write_mask_dict(new_mask)

    facade.switch_to_layer(saved_active)
    facade.finish(color_only=False)
    return True
