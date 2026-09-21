"""Detects and resolves weight mismatches between SuperSkinPro's layer storage and Blender's
native deform Vertex Groups."""

import hashlib

from ...core.facade import CoreFacade

_SIGNATURE_ID_KEY = "ssp_native_guard_sig"
_WEIGHT_EPSILON = 1e-5


def _real_weight_snapshot(obj) -> list:
    """Sorted `(v_idx, bone_name, weight)` rows describing the mesh's current real deform
    Vertex Group weights."""
    vg_names = {vg.index: vg.name for vg in obj.vertex_groups if not vg.name.startswith("__ssp_")}
    if not vg_names:
        return []
    rows = []
    for v in obj.data.vertices:
        for g in v.groups:
            name = vg_names.get(g.group)
            if name is None:
                continue
            w = round(g.weight, 5)
            if w <= _WEIGHT_EPSILON:
                continue
            rows.append((v.index, name, w))
    rows.sort()
    return rows


def compute_deform_signature(obj) -> str:
    """Hashes the mesh's current real deform Vertex Group weights into a
    short, stable signature string."""
    rows = _real_weight_snapshot(obj)
    payload = "|".join(f"{v}:{n}:{w}" for v, n, w in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_flatten_signature(obj) -> None:
    """Snapshots the mesh's current real deform weights as the new "last known SuperSkinPro-
    authored state", stored."""
    sig = compute_deform_signature(obj)
    obj.data[_SIGNATURE_ID_KEY] = sig


def has_native_mismatch(obj) -> bool:
    """True if the mesh's current real deform weights differ from the signature recorded at the
    last SuperSkinPro-authored write."""
    if obj is None or obj.type != 'MESH':
        return False
    stored = obj.data.get(_SIGNATURE_ID_KEY)
    if not stored:
        return False
    current = compute_deform_signature(obj)
    return stored != current


def import_native_into_active_layer(context, obj) -> None:
    """Overwrites the active layer's stored weights with the mesh's current real deform Vertex
    Group weights ("Keep Blender Native Weights")."""
    layer_dict = {}
    for v_idx, name, w in _real_weight_snapshot(obj):
        layer_dict.setdefault(v_idx, {})[name] = w
    facade = CoreFacade(context)
    facade.write_layer_dict(layer_dict)
    facade.finish(color_only=False)
