
import json

from ...core_subsystems.profiler.profiler_service import profile_section
from .bmesh_io import iter_group_weights, write_deform

PREFIX = "__ssp_"
_LAYER_PREFIX = "__ssp_L"
META_VG_NAME = "__ssp_meta"
META_LAYER_KEY = "__ssp_meta_layer"
META_MAP_KEY = "__ssp_meta_map"
SESSION_MARKER = "__ssp_wp_session"
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


def has_temp_vgs(obj) -> bool:
    return obj.vertex_groups.get(META_VG_NAME) is not None


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


def _write_new_group_entries(obj, entries: dict) -> None:
    if not entries:
        return
    if len(entries) < _BMESH_PUSH_MIN_VERTS:
        adds: dict = {}
        for v_idx, groups in entries.items():
            for group, w in groups.items():
                adds.setdefault((group, w), []).append(v_idx)
        apply_vg_deltas(obj.vertex_groups, {}, adds)
        return

    with write_deform(obj.data) as (bm, deform):
        for v_idx, groups in entries.items():
            dvert = bm.verts[v_idx][deform]
            for group, w in groups.items():
                dvert[group] = min(1.0, max(0.0, w))


def load_layer_to_temp_vgs(obj, layer_dict: dict, mask_dict: dict,
                           layer_idx: int, id_to_bone: dict,
                           mask_default: float = 0.0, *, weights_enabled: bool = True):
    num_verts = len(obj.data.vertices)

    delete_temp_vgs(obj, layer_idx=layer_idx)

    entries: dict = {}
    if weights_enabled:
        bone_to_group = {}
        for vg_idx, bone_name in id_to_bone.items():
            bone_to_group[bone_name] = obj.vertex_groups.new(name=weight_vg_name(layer_idx, vg_idx)).index
        for v_idx, v_weights in layer_dict.items():
            v_int = int(v_idx)
            if not 0 <= v_int < num_verts:
                continue
            for bone_name, weight in v_weights.items():
                group = bone_to_group.get(bone_name)
                if group is not None:
                    entries.setdefault(v_int, {})[group] = float(weight)

    mask_vg = obj.vertex_groups.new(name=mask_vg_name(layer_idx))
    mask_idx = mask_vg.index
    if mask_default > 0.0:
        for v in range(num_verts):
            entries.setdefault(v, {})[mask_idx] = float(mask_default)
    for v_idx, weight in mask_dict.items():
        v_int = int(v_idx)
        if 0 <= v_int < num_verts:
            entries.setdefault(v_int, {})[mask_idx] = float(weight)

    _write_new_group_entries(obj, entries)

    if obj.vertex_groups.get(META_VG_NAME) is None:
        obj.vertex_groups.new(name=META_VG_NAME)
        _purge_legacy_meta_attributes(obj.data)
    obj[META_LAYER_KEY] = layer_idx
    obj[META_MAP_KEY] = json.dumps({str(k): v for k, v in id_to_bone.items()})


def read_temp_vgs_to_layer(obj, layer_idx: int = None) -> tuple:
    meta_vg = obj.vertex_groups.get(META_VG_NAME)
    if meta_vg is None:
        return {}, {}, 0

    active_layer_index = temp_layer_index(obj)
    if layer_idx is None:
        layer_idx = active_layer_index

    idx_to_bone, _, mask_idx = _active_layer_vg_maps(obj, layer_idx)
    layer_dict, mask_dict = _read_layer_groups(obj.data, idx_to_bone, mask_idx)
    _prune_zero_weights(layer_dict)
    return layer_dict, mask_dict, active_layer_index


def _prune_zero_weights(layer_dict: dict):
    peak: dict = {}
    for v_weights in layer_dict.values():
        for bone_name, w in v_weights.items():
            if w > peak.get(bone_name, 0.0):
                peak[bone_name] = w
            else:
                peak.setdefault(bone_name, 0.0)

    zero_bones = {bone_name for bone_name, w in peak.items() if w <= 0.0001}

    if not zero_bones:
        return

    for v_weights in layer_dict.values():
        for bone_name in zero_bones:
            v_weights.pop(bone_name, None)

    empty_verts = [v for v, w in layer_dict.items() if not w]
    for v in empty_verts:
        del layer_dict[v]


def delete_temp_vgs(obj, layer_idx: int = None):
    if layer_idx is None:
        to_remove = [vg for vg in obj.vertex_groups if vg.name.startswith(PREFIX)]
        for vg in to_remove:
            obj.vertex_groups.remove(vg)
        obj.pop(META_LAYER_KEY, None)
        obj.pop(META_MAP_KEY, None)
    else:
        prefix = f"{_LAYER_PREFIX}{layer_idx}_"
        to_remove = [vg for vg in obj.vertex_groups if vg.name.startswith(prefix)]
        for vg in to_remove:
            obj.vertex_groups.remove(vg)


def get_active_layer_from_meta(obj) -> int:
    if not has_temp_vgs(obj):
        return -1
    return temp_layer_index(obj, -1)


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



_WP_EPSILON = 1e-5


def is_wp_session(obj) -> bool:
    return obj is not None and obj.mode == 'WEIGHT_PAINT' and has_temp_vgs(obj)


def _active_layer_vg_maps(obj, layer_idx: int):
    id_to_bone = temp_id_to_bone(obj)
    idx_to_bone = {}
    bone_to_idx = {}
    for vg in obj.vertex_groups:
        parsed = parse_layer_weight_vg_name(vg.name)
        if parsed is None or parsed[0] != layer_idx:
            continue
        bone = id_to_bone.get(parsed[1])
        if bone is None:
            continue
        idx_to_bone[vg.index] = bone
        bone_to_idx[bone] = vg.index
    mask_vg = obj.vertex_groups.get(mask_vg_name(layer_idx))
    return idx_to_bone, bone_to_idx, (mask_vg.index if mask_vg is not None else None)


def _read_layer_groups(mesh, idx_to_bone, mask_idx, vert_indices=None) -> tuple:
    layer_dict = {}
    mask_dict = {}
    for v_idx, items in iter_group_weights(mesh, vert_indices):
        entries = None
        for group, weight in items:
            bone = idx_to_bone.get(group)
            if bone is not None:
                if weight > 0.0:
                    if entries is None:
                        entries = layer_dict.setdefault(v_idx, {})
                    entries[bone] = weight
            elif group == mask_idx:
                mask_dict[v_idx] = weight
    return layer_dict, mask_dict


def read_active_layer_from_mesh(obj, dirty_verts=None, vg_maps: dict = None) -> tuple:
    layer_idx = temp_layer_index(obj)
    maps = vg_maps.get(layer_idx) if vg_maps else None
    idx_to_bone, _, mask_idx = maps if maps is not None else _active_layer_vg_maps(obj, layer_idx)
    layer_dict, mask_dict = _read_layer_groups(obj.data, idx_to_bone, mask_idx, dirty_verts)
    _prune_zero_weights(layer_dict)
    return layer_dict, mask_dict, layer_idx


def _normalize_mask_for_compare(d: dict) -> dict:
    return {int(v): round(float(w), 4) for v, w in d.items()}


def _normalize_weights(ws: dict) -> dict:
    return {k: round(float(w), 4) for k, w in ws.items() if float(w) > _WP_EPSILON}


def _layers_match(a: dict, b: dict) -> bool:
    if a == b:
        return True
    na = {int(v): ws for v, ws in a.items() if ws}
    nb = {int(v): ws for v, ws in b.items() if ws}
    if na.keys() != nb.keys():
        return False
    for v, ws in na.items():
        other = nb[v]
        if ws != other and _normalize_weights(ws) != _normalize_weights(other):
            return False
    return True


def _masks_match(a: dict, b: dict) -> bool:
    return a == b or _normalize_mask_for_compare(a) == _normalize_mask_for_compare(b)


_stored_layer_cache: dict = {}


def _read_stored_layer(storage, mesh_name: str, layer_idx: int) -> dict:
    raw = storage.read_layer_raw(layer_idx)
    cached = _stored_layer_cache.pop(mesh_name, None)
    if cached is not None and cached[0] == layer_idx and cached[1] == raw:
        return cached[2]
    return storage.read_layer_dict(layer_idx)


def _pull_dirty_to_storage(obj, storage, dirty_verts, vg_maps: dict = None) -> bool:
    size = len(dirty_verts)
    mesh_name = obj.data.name
    with profile_section("core.layer_storage.pull_dirty.read_temp", size):
        layer_dict, mask_dict, layer_idx = read_active_layer_from_mesh(obj, dirty_verts, vg_maps)
        mask_dict = _strip_mask_default(mask_dict, layer_mask_default(storage, layer_idx))
    with profile_section("core.layer_storage.pull_dirty.decode_stored", size):
        stored_layer = _read_stored_layer(storage, mesh_name, layer_idx)
        stored_mask = storage.read_mask_dict(layer_idx)
    layer_changed = mask_changed = False
    for v in dirty_verts:
        new_w = layer_dict.get(v)
        old_w = stored_layer.get(v)
        if new_w:
            if not _layers_match({v: new_w}, {v: old_w or {}}):
                stored_layer[v] = new_w
                layer_changed = True
        elif old_w:
            del stored_layer[v]
            layer_changed = True
        new_m = mask_dict.get(v)
        old_m = stored_mask.get(v)
        if new_m is not None:
            if old_m is None or round(float(new_m), 4) != round(float(old_m), 4):
                stored_mask[v] = new_m
                mask_changed = True
        elif old_m is not None:
            del stored_mask[v]
            mask_changed = True
    with profile_section("core.layer_storage.pull_dirty.encode_write", size):
        if layer_changed:
            storage.write_layer_dict(layer_idx, stored_layer)
        if mask_changed:
            if stored_mask:
                storage.write_mask_dict(layer_idx, stored_mask)
            else:
                storage.delete_mask_property(layer_idx)
    _stored_layer_cache[mesh_name] = (layer_idx, storage.read_layer_raw(layer_idx), stored_layer)
    return layer_changed or mask_changed


def pull_temp_to_storage(obj, storage, dirty_verts=None, vg_maps: dict = None) -> bool:
    active_idx = temp_layer_index(obj)
    if storage.find_layer_meta(active_idx) is None:
        return False
    if dirty_verts is not None:
        return _pull_dirty_to_storage(obj, storage, dirty_verts, vg_maps)
    layer_dict, mask_dict, layer_idx = read_active_layer_from_mesh(obj)
    mask_default = layer_mask_default(storage, layer_idx)
    mask_dict = _strip_mask_default(mask_dict, mask_default)
    changed = False
    if not _layers_match(layer_dict, storage.read_layer_dict(layer_idx)):
        storage.write_layer_dict(layer_idx, layer_dict)
        changed = True
    stored_mask = _strip_mask_default(storage.read_mask_dict(layer_idx), mask_default)
    if not _masks_match(mask_dict, stored_mask):
        if mask_dict:
            storage.write_mask_dict(layer_idx, mask_dict)
        else:
            storage.delete_mask_property(layer_idx)
        changed = True
    return changed


_BMESH_PUSH_MIN_VERTS = 256


def _push_via_bmesh(obj, layer_str, mask_dict, verts, idx_to_bone, bone_to_idx, mask_idx,
                    mask_default=0.0) -> None:
    write_layer = layer_str is not None
    write_mask = mask_dict is not None and mask_idx is not None

    with write_deform(obj.data) as (bm, deform):
        for v_idx in verts:
            dvert = bm.verts[v_idx][deform]
            if write_layer:
                desired = {bone_to_idx[b]: w for b, w in layer_str.get(v_idx, {}).items()
                           if b in bone_to_idx and w > 0.0}
                for group in [g for g in dvert.keys() if g in idx_to_bone and g not in desired]:
                    del dvert[group]
                for group, w in desired.items():
                    dvert[group] = float(w)
            if write_mask:
                want = mask_dict.get(v_idx, mask_default if mask_default > 0.0 else None)
                if want is None:
                    if mask_idx in dvert:
                        del dvert[mask_idx]
                else:
                    dvert[mask_idx] = float(want)


def apply_vg_deltas(vgs, removals: dict, adds: dict) -> None:
    for gi, v_list in removals.items():
        vgs[gi].remove(v_list)
    for (gi, w), v_list in adds.items():
        vgs[gi].add(v_list, w, 'REPLACE')


def push_layer_to_temp_vgs(obj, layer_str: dict, mask_dict: dict = None,
                           dirty_verts=None, live_cache: dict = None,
                           vg_maps: dict = None) -> None:
    layer_idx = temp_layer_index(obj)
    maps = vg_maps.get(layer_idx) if vg_maps is not None else None
    if maps is None:
        maps = _active_layer_vg_maps(obj, layer_idx)
        if vg_maps is not None:
            vg_maps[layer_idx] = maps
    idx_to_bone, bone_to_idx, mask_idx = maps
    mesh = obj.data
    vgs = obj.vertex_groups
    verts = range(len(mesh.vertices)) if dirty_verts is None else sorted(dirty_verts)

    mask_default = 0.0
    if mask_dict is not None:
        from .storage_service import LayerStorageService
        mask_default = layer_mask_default(LayerStorageService(mesh), layer_idx)

    if live_cache is None and len(verts) >= _BMESH_PUSH_MIN_VERTS:
        _push_via_bmesh(obj, layer_str, mask_dict, verts, idx_to_bone, bone_to_idx, mask_idx,
                        mask_default)
        return

    removals: dict = {}
    adds: dict = {}
    for v_idx in (verts if layer_str is not None else ()):
        current = live_cache.get(v_idx) if live_cache is not None else None
        if current is None:
            current = {g.group: g.weight for g in mesh.vertices[v_idx].groups if g.group in idx_to_bone}
        desired = {bone_to_idx[b]: float(w) for b, w in layer_str.get(v_idx, {}).items()
                   if b in bone_to_idx and w > 0.0}
        for gi in current.keys() - desired.keys():
            removals.setdefault(gi, []).append(v_idx)
        for gi, w in desired.items():
            if abs(current.get(gi, -1.0) - w) > _WP_EPSILON:
                adds.setdefault((gi, w), []).append(v_idx)
        if live_cache is not None:
            live_cache[v_idx] = desired
    apply_vg_deltas(vgs, removals, adds)

    if mask_dict is not None and mask_idx is not None:
        mask_removals: dict = {}
        mask_adds: dict = {}
        for v_idx in verts:
            cur = next((g.weight for g in mesh.vertices[v_idx].groups if g.group == mask_idx), None)
            want = mask_dict.get(v_idx, mask_default if mask_default > 0.0 else None)
            if want is None:
                if cur is not None:
                    mask_removals.setdefault(mask_idx, []).append(v_idx)
            elif cur is None or abs(cur - want) > _WP_EPSILON:
                mask_adds.setdefault((mask_idx, float(want)), []).append(v_idx)
        apply_vg_deltas(vgs, mask_removals, mask_adds)


def load_stored_layer_to_temp_vgs(obj, storage, layer_idx: int) -> None:
    _, id_to_bone = storage.get_unified_mapping(obj)
    load_layer_to_temp_vgs(obj, storage.read_layer_dict(layer_idx),
                           storage.read_mask_dict(layer_idx), layer_idx, id_to_bone,
                           layer_mask_default(storage, layer_idx),
                           weights_enabled=not is_group_layer(storage, layer_idx))


def reload_active_layer_temp_vgs(obj, storage) -> None:
    load_stored_layer_to_temp_vgs(obj, storage, temp_layer_index(obj))
