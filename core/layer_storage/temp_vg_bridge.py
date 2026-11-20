
from itertools import chain

import numpy as np

from ...core_subsystems.layer_compositor import LayerCompositor as _LC
from ...core_subsystems.profiler.profiler_service import profile_section
from .session_meta import (
    DEFORM_GEN_KEY, META_LAYER_KEY, META_MAP_KEY, META_VG_NAME, PREFIX, SESSION_CONTEXT_MODE,
    SESSION_MARKER, SESSION_MODE, _INFLUENCE_EPSILON, _LAYER_PREFIX, _LEGACY_META_ATTRIBUTES,
    _MASK_STATE_CACHE_MAX, _loaded_influence, _mark_session_layer, _mask_state_cache,
    _mask_values_close, _prune_zero_weights, _purge_legacy_meta_attributes, _split_by_surface,
    _strip_mask_default, _surface_names, _weighted_bones, get_layer_mask_state, has_layer_cache,
    has_session_layer, has_temp_vgs, in_session, is_group_layer, is_paint_session,
    layer_mask_default, loaded_influence, mask_vg_name, parse_layer_weight_vg_name,
    remember_loaded_influence, rename_session_bones, session_surface, start_session_layer,
    switch_session_layer, temp_id_to_bone, temp_layer_index, weight_vg_name,
)
from .stored_layer_cache import (
    _read_stored_layer, _readonly_layer_cache, _remember_readonly_layer, _stored_layer_cache,
    peek_stored_layer, read_stored_layer_readonly,
)
from .group_writes import (
    GroupWriteState, _read_groups, surface_rows_coo, surface_rows_int, targets_coo,
    write_group_entries,
)
from ...core_subsystems.layer_compositor.weight_floor import WEIGHT_FLOOR
from .legacy_temp_vgs import (
    _WP_EPSILON, _active_layer_vg_maps, _layer_matches, _layers_match, _mask_entries, _masks_match,
    _normalize_mask_for_compare, _normalize_weights, _pull_full_bulk, _pull_layer_bulk,
    _pull_mask_bulk, _read_column, _read_layer_groups, _round4_keys, _stored_bone_spans,
    delete_temp_vgs, pull_temp_to_storage, read_active_layer_from_mesh, read_temp_vgs_to_layer,
)


class WrittenRows:
    """Per stroke, the active Layer's ``{bone_id: weight}`` row last written on each vertex, kept
    as dict rows or as one COO chunk per write."""

    _DICT = -2

    def __init__(self):
        self._last = np.full(0, -1, dtype=np.int32)
        self._dict = {}
        self._chunks = []

    def _mark(self, verts, tag):
        if len(verts):
            top = int(verts.max()) + 1
            if top > len(self._last):
                grown = np.full(max(top, 2 * len(self._last)), -1, dtype=np.int32)
                grown[:len(self._last)] = self._last
                self._last = grown
            self._last[verts] = tag

    def update(self, rows: dict) -> None:
        self._dict.update(rows)
        self._mark(np.fromiter(rows, dtype=np.int64, count=len(rows)), self._DICT)

    def add_coo(self, verts, vert_ids, groups, weights) -> None:
        self._chunks.append((verts, np.asarray(vert_ids, dtype=np.int64),
                             np.asarray(groups, dtype=np.int64), np.asarray(weights, dtype=np.float64)))
        self._mark(verts, len(self._chunks) - 1)

    def marked(self):
        return np.flatnonzero(self._last != -1).astype(np.int64)

    def covers(self, verts) -> bool:
        verts = np.fromiter(verts, dtype=np.int64, count=len(verts))
        if not len(verts):
            return True
        if verts.min() < 0 or verts.max() >= len(self._last):
            return False
        return bool((self._last[verts] != -1).all())

    def rows_coo(self, verts):
        tags = self._last[verts]
        parts = []
        for tag in np.unique(tags).tolist():
            if tag == -1:
                continue
            mine = verts[tags == tag]
            if tag == self._DICT:
                rows = [self._dict[v] for v in mine.tolist()]
                lens = np.fromiter(map(len, rows), dtype=np.int64, count=len(rows))
                total = int(lens.sum())
                parts.append((np.repeat(mine, lens),
                              np.fromiter(chain.from_iterable(rows), dtype=np.int64, count=total),
                              np.fromiter(chain.from_iterable(r.values() for r in rows),
                                          dtype=np.float64, count=total)))
                continue
            _chunk_verts, cv, cg, cw = self._chunks[tag]
            inside = np.isin(cv, mine)
            parts.append((cv[inside], cg[inside], cw[inside]))
        if not parts:
            return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64), np.empty(0)
        vert_ids, groups, weights = (np.concatenate(cols) for cols in zip(*parts))
        order = np.argsort(vert_ids, kind="stable")
        return vert_ids[order], groups[order], weights[order]

    def __contains__(self, v):
        return 0 <= v < len(self._last) and self._last[v] != -1


def _layer_from_written(verts, written, id_to_bone: dict) -> dict:
    vert_ids, bone_ids, weights = written.rows_coo(verts)
    stored = weights.astype(np.float32).astype(np.float64).tolist()
    layer_dict = {}
    for v, b, w in zip(vert_ids.tolist(), bone_ids.tolist(), stored):
        name = id_to_bone.get(b)
        if name is None:
            continue
        row = layer_dict.get(v)
        if row is None:
            row = layer_dict[v] = {}
        row[name] = w
    _prune_zero_weights(layer_dict)
    return layer_dict


def _written_rows_csr(verts, written, id_to_bone: dict) -> tuple:
    vert_ids, bone_ids, weights = written.rows_coo(verts)
    stored = weights.astype(np.float32).astype(np.float64)
    alive = set(np.unique(bone_ids[stored > WEIGHT_FLOOR]).tolist())
    kept = [b for b in np.unique(bone_ids).tolist() if b in alive and id_to_bone.get(b) is not None]
    kept_ids = np.array(kept, dtype=np.int64)
    keep = np.isin(bone_ids, kept_ids)
    row_lens = np.bincount(np.searchsorted(verts, vert_ids[keep]), minlength=len(verts))
    return (row_lens, [id_to_bone[b] for b in kept], np.searchsorted(kept_ids, bone_ids[keep]),
            stored[keep])


def _merge_written_rows(stored_layer: dict, verts, written, id_to_bone: dict, surface) -> bool:
    layer_dict = _layer_from_written(verts, written, id_to_bone)
    changed = False
    for v in verts.tolist():
        new_w = layer_dict.get(v)
        old_w, off_surface = _split_by_surface(stored_layer.get(v), surface)
        if new_w:
            if new_w != old_w:
                stored_layer[v] = {**off_surface, **new_w}
                changed = True
        elif old_w:
            if off_surface:
                stored_layer[v] = off_surface
            else:
                del stored_layer[v]
            changed = True
    return changed


def commit_stroke_rows(obj, storage, dirty_verts, written, id_to_bone: dict,
                       mask_dict: dict = None) -> bool:
    layer_idx = temp_layer_index(obj)
    if storage.find_layer_meta(layer_idx) is None:
        return False
    size = len(dirty_verts)
    mesh_name = obj.data.name
    with profile_section("core.layer_storage.commit.rows", size):
        rows_verts = np.empty(0, dtype=np.int64)
        if written is not None:
            dirty = np.fromiter(dirty_verts, dtype=np.int64, count=len(dirty_verts))
            rows_verts = np.sort(dirty[np.isin(dirty, written.marked())])
        new_mask = None
        if mask_dict is not None:
            new_mask = _strip_mask_default({v: mask_dict[v] for v in dirty_verts if v in mask_dict},
                                           layer_mask_default(storage, layer_idx))
    with profile_section("core.layer_storage.commit.decode_stored", size):
        stored_layer = _read_stored_layer(storage, mesh_name, layer_idx)
        stored_mask = storage.read_mask_dict(layer_idx) if new_mask is not None else None
    as_arrays = not isinstance(stored_layer, dict)
    surface = _surface_names(obj)
    layer_changed = False
    with profile_section("core.layer_storage.commit.merge_rows", size):
        if len(rows_verts) and as_arrays:
            stored_layer, layer_changed = _LC.merge_rows(
                stored_layer, rows_verts, *_written_rows_csr(rows_verts, written, id_to_bone),
                surface)
        elif len(rows_verts):
            layer_changed = _merge_written_rows(stored_layer, rows_verts, written, id_to_bone,
                                                surface)
    mask_changed = False
    if new_mask is not None:
        for v in dirty_verts:
            new_m = new_mask.get(v)
            old_m = stored_mask.get(v)
            if new_m is not None:
                if old_m is None or round(float(new_m), 4) != round(float(old_m), 4):
                    stored_mask[v] = new_m
                    mask_changed = True
            elif old_m is not None:
                del stored_mask[v]
                mask_changed = True
    with profile_section("core.layer_storage.commit.encode_write", size):
        if layer_changed and as_arrays:
            stored_layer = storage.write_layer_arrays(layer_idx, stored_layer, fast=True)
        elif layer_changed:
            storage.write_layer_dict(layer_idx, stored_layer, fast=True)
            stored_layer = storage.read_layer_arrays(layer_idx) or stored_layer
        if mask_changed:
            if stored_mask:
                storage.write_mask_dict(layer_idx, stored_mask)
            else:
                storage.delete_mask_property(layer_idx)
    _stored_layer_cache[mesh_name] = (layer_idx, storage.read_layer_raw(layer_idx), stored_layer)
    return layer_changed or mask_changed
