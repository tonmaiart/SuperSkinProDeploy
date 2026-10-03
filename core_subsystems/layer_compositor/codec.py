
import functools
import pickle
import time
import zlib
import base64

from ..rust_weight_engine import RustWeightEngine
from ..rust_weight_engine.flat_array_bridge import build_local_bone_ids, layer_to_coo_sparse
from ..profiler import ProfilerService


MAGIC_STR = "SSLY"


def decode_layer_dict(raw):
    if not raw or not isinstance(raw, str) or not raw.startswith(MAGIC_STR):
        return {}
    try:
        compressed = base64.b64decode(raw[4:])
        return pickle.loads(zlib.decompress(compressed))
    except Exception:
        return {}


def encode_layer_dict(layer_dict):
    raw = pickle.dumps(layer_dict, protocol=pickle.HIGHEST_PROTOCOL)
    compressed = zlib.compress(raw, level=1)
    return MAGIC_STR + base64.b64encode(compressed).decode("ascii")


def mask_entry_value(raw) -> float:
    if isinstance(raw, dict):
        raw = next(iter(raw.values()), 0.0)
    if isinstance(raw, (int, float)):
        return float(raw)
    return 0.0


def normalize_mask_dict(mask_dict) -> dict:
    return {int(v): mask_entry_value(w) for v, w in mask_dict.items()}


def mask_value(raw):
    return 1.0 if raw is None else mask_entry_value(raw)



@functools.lru_cache(maxsize=32)
def _decode_and_normalize_mask_cached(raw):
    return normalize_mask_dict(decode_layer_dict(raw))


@functools.lru_cache(maxsize=32)
def _decode_and_coo_cached(raw):
    decoded = decode_layer_dict(raw)
    if not decoded:
        return None, None, None, None
    layer_int = {int(v): w for v, w in decoded.items()}
    bone_to_id, id_to_bone = build_local_bone_ids(layer_int)
    vert_ids, bone_ids, weights = layer_to_coo_sparse(layer_int, bone_to_id)
    return vert_ids, bone_ids, weights, id_to_bone


def _harvest_mask(raw_mask) -> dict:
    if not raw_mask:
        return {}
    if isinstance(raw_mask, dict):
        return normalize_mask_dict(raw_mask)
    return _decode_and_normalize_mask_cached(raw_mask)



def _composite_layers(meta_list, layer_data_map, mask_data_map, idx_to_name, num_verts,
                      dirty_verts=None):
    _profile_metrics = ProfilerService.is_enabled()
    _t0 = time.perf_counter() if _profile_metrics else None

    rust = RustWeightEngine("layer_compositor")

    meta_clean = []
    mask_decoded_map = {}
    dict_layer_data_map = {}
    coo_vert_ids_map = {}
    coo_bone_ids_map = {}
    coo_weights_map = {}
    coo_id_to_bone_map = {}
    foundation_seen = False

    group_mask_lookup = {}
    for g in meta_list:
        if not g.get("is_group"):
            continue
        g_idx = int(g["index"])
        if not g.get("visible", True):
            group_mask_lookup[g_idx] = ({}, 0.0)
            continue
        group_mask_lookup[g_idx] = (_harvest_mask(mask_data_map.get(g_idx)),
                                    float(g.get("mask_default", 1.0)))

    for layer in reversed(meta_list):
        if layer.get("is_group") or not layer.get("visible", True):
            continue
        l_idx = int(layer["index"])
        raw_layer = layer_data_map.get(l_idx)
        if not raw_layer:
            continue
        if isinstance(raw_layer, dict):
            dict_layer_data_map[l_idx] = {int(v): w for v, w in raw_layer.items()}
        else:
            vert_ids, bone_ids, weights, id_to_bone = _decode_and_coo_cached(raw_layer)
            if vert_ids is None:
                continue
            coo_vert_ids_map[l_idx] = vert_ids
            coo_bone_ids_map[l_idx] = bone_ids
            coo_weights_map[l_idx] = weights
            coo_id_to_bone_map[l_idx] = id_to_bone

        mask_default = float(layer.get("mask_default", 1.0))
        own_mask = _harvest_mask(mask_data_map.get(l_idx))

        group_id = layer.get("group_id")
        group_entry = group_mask_lookup.get(group_id) if group_id is not None else None
        if group_entry is not None:
            group_dict, group_default = group_entry
            own_default_c = max(0.0, min(1.0, mask_default))
            group_default_c = max(0.0, min(1.0, group_default))
            mask_default = own_default_c * group_default_c
            own_mask = {
                v: max(0.0, min(1.0, own_mask.get(v, own_default_c)))
                * max(0.0, min(1.0, group_dict.get(v, group_default_c)))
                for v in set(own_mask) | set(group_dict)
            }

        meta_clean.append({
            "index": float(l_idx),
            "mask_default": float(mask_default),
            "is_base": 0.0 if foundation_seen else 1.0,
        })
        foundation_seen = True
        mask_decoded_map[l_idx] = own_mask

    if _profile_metrics:
        _t_harvest = time.perf_counter()

    args = [meta_clean, dict_layer_data_map, coo_vert_ids_map, coo_bone_ids_map,
            coo_weights_map, coo_id_to_bone_map, mask_decoded_map, num_verts]
    if dirty_verts is not None:
        args.append(list(dirty_verts))
    result = rust.call("rust_composite_layers_mixed", *args)

    if _profile_metrics:
        _t_rust = time.perf_counter()
        _base = "core_subsystems.layer_compositor.composite_layers"
        _size = len(dirty_verts) if dirty_verts is not None else num_verts
        ProfilerService.record(f"{_base}.harvest", 1000 * (_t_harvest - _t0), _size)
        ProfilerService.record(f"{_base}.rust_call", 1000 * (_t_rust - _t_harvest), _size)
        ProfilerService.record(f"{_base}.total", 1000 * (_t_rust - _t0), _size)

    return result
