
import array

import numpy as np

from ...core_subsystems.profiler.profiler_service import profile_section
from . import group_arrays
from .bmesh_io import iter_group_weights
from .session_meta import (
    META_LAYER_KEY, META_MAP_KEY, META_VG_NAME, PREFIX, _LAYER_PREFIX, _prune_zero_weights,
    _split_by_surface, _strip_mask_default, _surface_names, has_temp_vgs, layer_mask_default,
    mask_vg_name, parse_layer_weight_vg_name, temp_id_to_bone, temp_layer_index,
)
from .stored_layer_cache import _remember_readonly_layer, read_stored_layer_readonly
from ...core_subsystems.layer_compositor.weight_floor import WEIGHT_FLOOR


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

_WP_EPSILON = 1e-5


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


def pull_temp_to_storage(obj, storage) -> bool:
    if not has_temp_vgs(obj):
        return False
    active_idx = temp_layer_index(obj)
    if storage.find_layer_meta(active_idx) is None:
        return False
    if group_arrays.use_bulk(obj.data):
        changed = _pull_full_bulk(obj, storage, active_idx)
        if changed is not None:
            return changed
    with profile_section("core.layer_storage.pull_full.read_temp"):
        layer_dict, mask_dict, layer_idx = read_active_layer_from_mesh(obj)
    mask_default = layer_mask_default(storage, layer_idx)
    mask_dict = _strip_mask_default(mask_dict, mask_default)
    changed = False
    with profile_section("core.layer_storage.pull_full.compare"):
        surface = _surface_names(obj)
        on_surface, off_surface = {}, {}
        for v, row in read_stored_layer_readonly(storage, layer_idx).items():
            on, off = _split_by_surface(row, surface)
            if on:
                on_surface[v] = on
            if off:
                off_surface[v] = off
        layer_matches = _layers_match(layer_dict, on_surface)
    if not layer_matches:
        for v, off in off_surface.items():
            layer_dict[v] = {**off, **layer_dict.get(v, {})}
        storage.write_layer_dict(layer_idx, layer_dict, fast=True)
        _remember_readonly_layer(storage, layer_idx, layer_dict)
        changed = True
    stored_mask = _strip_mask_default(storage.read_mask_dict(layer_idx), mask_default)
    if not _masks_match(mask_dict, stored_mask):
        if mask_dict:
            storage.write_mask_dict(layer_idx, mask_dict)
        else:
            storage.delete_mask_property(layer_idx)
        changed = True
    return changed


def _round4_keys(values) -> np.ndarray:
    scaled = np.asarray(values, dtype=np.float64) * 10000.0
    keys = np.rint(scaled)
    near_half = np.abs(scaled - np.floor(scaled) - 0.5) < 1e-6
    for i in np.flatnonzero(near_half).tolist():
        keys[i] = round(round(float(values[i]), 4) * 10000.0)
    return keys.astype(np.int64)


def _stored_bone_spans(arrays, bones):
    names, vert_ids, row_lens, name_idx, weights = arrays
    slot_of = {bone: i for i, bone in enumerate(bones)}
    slots = np.array([slot_of.get(n, -1) for n in names], dtype=np.int64)
    slots = slots[np.asarray(name_idx, dtype=np.int64)] if len(slots) else \
        np.zeros(0, dtype=np.int64)
    verts = np.repeat(np.asarray(vert_ids, dtype=np.int64), np.asarray(row_lens, dtype=np.int64))
    weights = np.asarray(weights, dtype=np.float64)
    if len(bones) <= 16:
        return verts, weights, [slots == i for i in range(len(bones))]
    picked = slots >= 0
    order = np.argsort(slots[picked], kind="stable")
    slots = slots[picked][order]
    bounds = np.searchsorted(slots, np.arange(len(bones) + 1)).tolist()
    spans = [slice(bounds[i], bounds[i + 1]) for i in range(len(bones))]
    return verts[picked][order], weights[picked][order], spans


def _read_column(read, groups, bone, num_verts) -> np.ndarray:
    column = None
    for name, read_bone in groups:
        if read_bone == bone:
            column = read[name]
    if column is None or not (column > WEIGHT_FLOOR).any():
        return np.zeros(num_verts, dtype=np.float32)
    return column


def _layer_matches(read, groups, arrays, surface, num_verts) -> bool:
    bones = list(dict.fromkeys([b for _n, b in groups] + [n for n in arrays[0] if n in surface]))
    verts, weights, spans = _stored_bone_spans(arrays, bones)
    on_surface = np.zeros(len(verts), dtype=bool)
    for span in spans:
        on_surface[span] = True
    if on_surface.any() and (verts[on_surface].min() < 0 or verts[on_surface].max() >= num_verts):
        return False
    read_rows = np.zeros(num_verts, dtype=bool)
    stored_rows = np.zeros(num_verts, dtype=bool)
    stored = np.zeros(num_verts)
    for bone, span in zip(bones, spans):
        column = _read_column(read, groups, bone, num_verts)
        read_rows |= column > 0.0
        stored[:] = 0.0
        stored[verts[span]] = weights[span]
        stored_rows[verts[span]] = True
        present = column > _WP_EPSILON
        if not np.array_equal(present, stored > _WP_EPSILON):
            return False
        at = np.flatnonzero(present)
        if not np.array_equal(_round4_keys(column[at]), _round4_keys(stored[at])):
            return False
    return np.array_equal(read_rows, stored_rows)


def _pull_layer_bulk(storage, layer_idx: int, arrays, surface, read, groups, num_verts) -> bool:
    if arrays is None:
        arrays = ([], array.array('q'), array.array('I'), array.array('I'), array.array('d'))
    if _layer_matches(read, groups, arrays, surface, num_verts):
        return False
    names, vert_ids, row_lens, name_idx, weights = arrays
    bones = list(dict.fromkeys(b for _n, b in groups))
    table = list(dict.fromkeys(list(names) + bones))
    id_of = {n: i for i, n in enumerate(table)}
    name_idx = np.asarray(name_idx, dtype=np.int64)
    if len(names):
        off = np.array([n not in surface for n in names], dtype=bool)[name_idx]
        stored_ids = np.array([id_of[n] for n in names], dtype=np.int64)[name_idx]
    else:
        off = np.zeros(0, dtype=bool)
        stored_ids = np.zeros(0, dtype=np.int64)
    parts_verts = [np.repeat(np.asarray(vert_ids, dtype=np.int64),
                             np.asarray(row_lens, dtype=np.int64))[off]]
    parts_ids = [stored_ids[off]]
    parts_weights = [np.asarray(weights, dtype=np.float64)[off]]
    for bone in bones:
        column = _read_column(read, groups, bone, num_verts)
        nonzero = np.flatnonzero(column > 0.0)
        parts_verts.append(nonzero)
        parts_ids.append(np.full(len(nonzero), id_of[bone], dtype=np.int64))
        parts_weights.append(column[nonzero].astype(np.float64))
    all_verts = np.concatenate(parts_verts)
    order = np.argsort(all_verts, kind="stable")
    all_verts = all_verts[order]
    all_ids = np.concatenate(parts_ids)[order]
    all_weights = np.concatenate(parts_weights)[order]
    row_verts, row_lens = np.unique(all_verts, return_counts=True)
    used = np.unique(all_ids)
    remap = np.full(len(table), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    storage.write_layer_arrays(layer_idx, ([table[i] for i in used.tolist()], row_verts, row_lens,
                                           remap[all_ids], all_weights), fast=True)
    return True


def _mask_entries(verts, values, mask_default: float) -> tuple:
    values = np.asarray(values, dtype=np.float64)
    if mask_default > 0.0:
        keep = np.abs(values - mask_default) >= 1e-4
    else:
        keep = values != 0.0
    return np.asarray(verts, dtype=np.int64)[keep], values[keep]


def _pull_mask_bulk(storage, layer_idx: int, read_mask, mask_default: float) -> bool:
    if read_mask is None:
        read_verts, read_values = np.zeros(0, dtype=np.int64), np.zeros(0)
    else:
        read_verts, read_values = _mask_entries(np.arange(len(read_mask)), read_mask, mask_default)
    stored_verts, stored_values = _mask_entries(*storage.read_mask_arrays(layer_idx), mask_default)
    read_order = np.argsort(read_verts, kind="stable")
    stored_order = np.argsort(stored_verts, kind="stable")
    if (np.array_equal(read_verts[read_order], stored_verts[stored_order])
            and np.array_equal(_round4_keys(read_values[read_order]),
                               _round4_keys(stored_values[stored_order]))):
        return False
    if len(read_verts):
        storage.write_mask_dict(layer_idx, dict(zip(read_verts.tolist(), read_values.tolist())))
    else:
        storage.delete_mask_property(layer_idx)
    return True


def _pull_full_bulk(obj, storage, layer_idx: int):
    arrays = storage.read_layer_arrays(layer_idx)
    if arrays is None and storage.read_layer_raw(layer_idx):
        return None
    idx_to_bone, _, mask_idx = _active_layer_vg_maps(obj, layer_idx)
    vgs = obj.vertex_groups
    groups = [(vgs[g].name, bone) for g, bone in idx_to_bone.items()]
    mask_name = vgs[mask_idx].name if mask_idx is not None else None
    with profile_section("core.layer_storage.pull_bulk.read_temp"):
        read = group_arrays.read_group_weights(
            obj, [name for name, _b in groups] + ([mask_name] if mask_name else []))
    if read is None:
        return None
    with profile_section("core.layer_storage.pull_bulk.compare_write"):
        layer_changed = _pull_layer_bulk(storage, layer_idx, arrays, _surface_names(obj), read,
                                         groups, len(obj.data.vertices))
        read_mask = read.get(mask_name) if mask_name else None
        mask_changed = _pull_mask_bulk(storage, layer_idx, read_mask,
                                       layer_mask_default(storage, layer_idx))
    return layer_changed or mask_changed
