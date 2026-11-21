
import json

import numpy as np
from ...core_subsystems.layer_compositor.weight_floor import WEIGHT_FLOOR

PREFIX = "__ssp_"
_LAYER_PREFIX = "__ssp_L"
META_VG_NAME = "__ssp_meta"
META_LAYER_KEY = "__ssp_meta_layer"
META_MAP_KEY = "__ssp_meta_map"
SESSION_MARKER = "__ssp_wp_session"
SESSION_MODE = 'OBJECT'
SESSION_CONTEXT_MODE = 'OBJECT'
DEFORM_GEN_KEY = "__ssp_deform_gen"

_LEGACY_META_ATTRIBUTES = ("__ssp_meta_list_len",) + tuple(
    f"__ssp_meta_list_chunk_{i}" for i in range(10)
)


def temp_layer_index(obj, default: int = 0) -> int:
    return int(obj.get(META_LAYER_KEY, default))


def temp_id_to_bone(obj) -> dict:
    try:
        return {int(k): v for k, v in json.loads(obj.get(META_MAP_KEY, "{}")).items()}
    except Exception:
        return {}


def rename_session_bones(obj, renames: dict) -> None:
    id_to_bone = temp_id_to_bone(obj)
    if any(name in renames for name in id_to_bone.values()):
        obj[META_MAP_KEY] = json.dumps({str(k): renames.get(v, v) for k, v in id_to_bone.items()})
    _loaded_influence.pop(obj.name, None)


def has_temp_vgs(obj) -> bool:
    return obj.vertex_groups.get(META_VG_NAME) is not None


def has_session_layer(obj) -> bool:
    return META_MAP_KEY in obj or has_temp_vgs(obj)


def has_layer_cache(obj, layer_idx: int) -> bool:
    return obj.vertex_groups.get(mask_vg_name(layer_idx)) is not None


def weight_vg_name(layer_idx: int, vg_idx: int) -> str:
    return f"{_LAYER_PREFIX}{layer_idx}_{vg_idx}"


def mask_vg_name(layer_idx: int) -> str:
    return f"{_LAYER_PREFIX}{layer_idx}_m"


def parse_layer_weight_vg_name(vg_name: str):
    if not vg_name.startswith(_LAYER_PREFIX):
        return None
    rest = vg_name[len(_LAYER_PREFIX):]
    if "_" not in rest:
        return None
    layer_part, suffix = rest.split("_", 1)
    try:
        return int(layer_part), int(suffix)
    except ValueError:
        return None


def _purge_legacy_meta_attributes(mesh) -> None:
    for name in _LEGACY_META_ATTRIBUTES:
        attr = mesh.attributes.get(name)
        if attr is None:
            continue
        try:
            mesh.attributes.remove(attr)
        except RuntimeError:
            pass


def layer_mask_default(storage, layer_idx: int) -> float:
    layer = storage.find_layer_meta(layer_idx)
    return float(layer.get("mask_default", 1.0)) if layer is not None else 1.0


def is_group_layer(storage, layer_idx: int) -> bool:
    layer = storage.find_layer_meta(layer_idx)
    return bool(layer.get("is_group")) if layer is not None else False


def _strip_mask_default(mask_dict: dict, mask_default: float) -> dict:
    if mask_default <= 0.0:
        return mask_dict
    return {v: w for v, w in mask_dict.items() if not _mask_values_close(float(w), mask_default)}


def _mark_session_layer(obj, layer_idx: int, id_to_bone: dict) -> None:
    if META_MAP_KEY not in obj:
        _purge_legacy_meta_attributes(obj.data)
    obj[META_LAYER_KEY] = layer_idx
    obj[META_MAP_KEY] = json.dumps({str(k): v for k, v in id_to_bone.items()})


def start_session_layer(obj, storage, layer_idx: int) -> None:
    _, id_to_bone = storage.get_unified_mapping(obj)
    _mark_session_layer(obj, layer_idx, id_to_bone)


_INFLUENCE_EPSILON = WEIGHT_FLOOR

_loaded_influence: dict = {}


def remember_loaded_influence(obj, layer_idx: int, bones, generation: int) -> None:
    _loaded_influence[obj.name] = (layer_idx, generation, frozenset(bones))


def loaded_influence(obj, layer_idx: int, generation: int):
    entry = _loaded_influence.get(obj.name)
    if entry is None or entry[0] != layer_idx or entry[1] != generation:
        return None
    return entry[2]


def _weighted_bones(storage, layer_idx: int) -> frozenset:
    arrays = storage.read_layer_arrays(layer_idx)
    if arrays is not None:
        names, _verts, _lens, name_idx, weights = arrays
        if not len(weights):
            return frozenset()
        above = np.asarray(name_idx)[np.asarray(weights) > _INFLUENCE_EPSILON]
        return frozenset(names[i] for i in np.unique(above).tolist())
    return frozenset(b for v_weights in storage.read_layer_dict(layer_idx).values()
                     for b, w in v_weights.items() if w > _INFLUENCE_EPSILON)


def switch_session_layer(obj, storage, new_idx: int) -> frozenset:
    start_session_layer(obj, storage, new_idx)
    if is_group_layer(storage, new_idx):
        return frozenset()
    return _weighted_bones(storage, new_idx)


def _prune_zero_weights(layer_dict: dict):
    seen: set = set()
    alive: set = set()
    for v_weights in layer_dict.values():
        if not v_weights:
            continue
        seen.update(v_weights)
        if min(v_weights.values()) > WEIGHT_FLOOR:
            alive.update(v_weights)
        else:
            alive.update(b for b, w in v_weights.items() if w > WEIGHT_FLOOR)

    zero_bones = seen - alive

    if not zero_bones:
        return

    for v_weights in layer_dict.values():
        for bone_name in zero_bones:
            v_weights.pop(bone_name, None)

    empty_verts = [v for v, w in layer_dict.items() if not w]
    for v in empty_verts:
        del layer_dict[v]


def _mask_values_close(value: float, target: float) -> bool:
    return abs(value - target) < 1e-4


_mask_state_cache: dict = {}
_MASK_STATE_CACHE_MAX = 256


def get_layer_mask_state(obj, storage, layer_index: int) -> str:
    layer_meta = storage.find_layer_meta(layer_index)
    if layer_meta is None:
        return 'EDITED'
    mask_default = float(layer_meta.get("mask_default", 1.0))
    num_verts = len(obj.data.vertices)

    signature = (storage.read_mask_raw(layer_index), mask_default, num_verts)
    cache_key = (obj.data.as_pointer(), layer_index)
    cached = _mask_state_cache.get(cache_key)
    if cached is not None and cached[0] == signature:
        return cached[1]

    mask_dict = storage.read_mask_dict(layer_index)
    values = set(mask_dict.values())
    if len(mask_dict) < num_verts:
        values.add(mask_default)

    if all(_mask_values_close(v, 1.0) for v in values):
        state = 'WHITE'
    elif all(_mask_values_close(v, 0.0) for v in values):
        state = 'BLACK'
    else:
        state = 'EDITED'
    if len(_mask_state_cache) >= _MASK_STATE_CACHE_MAX:
        _mask_state_cache.clear()
    _mask_state_cache[cache_key] = (signature, state)
    return state


def in_session(obj) -> bool:
    return (obj is not None and obj.type == 'MESH' and obj.mode == SESSION_MODE
            and SESSION_MARKER in obj)


def is_paint_session(obj) -> bool:
    return in_session(obj) and has_session_layer(obj)


def _surface_names(obj) -> frozenset:
    return frozenset(temp_id_to_bone(obj).values())


def _split_by_surface(row, surface) -> tuple:
    if not row:
        return {}, {}
    on, off = {}, {}
    for n, w in row.items():
        (on if n in surface else off)[n] = w
    return on, off


def session_surface(obj) -> frozenset:
    return _surface_names(obj)
