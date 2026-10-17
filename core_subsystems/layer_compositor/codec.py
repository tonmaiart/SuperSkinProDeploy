
import array
import functools
import pickle
import struct
import sys
import time
import zlib
import base64
from itertools import chain

import numpy as np

from ..rust_weight_engine import RustWeightEngine
from ..rust_weight_engine.flat_array_bridge import build_local_bone_ids, layer_to_coo_sparse
from ..profiler import ProfilerService


MAGIC_STR = "SSLY"
CSR_MAGIC = "SSLB"
_CSR_VERSION = 1
_CSR_HEADER = struct.Struct("<IIII")


def _little_endian(arr):
    if sys.byteorder != "little":
        arr.byteswap()
    return arr


def _encode_csr(layer_dict):
    try:
        rows = list(layer_dict.values())
        verts = array.array('q', layer_dict.keys())
        lens = array.array('I', map(len, rows))
        names_flat = list(chain.from_iterable(rows))
        weights = array.array('d', chain.from_iterable(map(dict.values, rows)))
    except (TypeError, OverflowError):
        return None
    table = list(dict.fromkeys(names_flat))
    if not all(type(n) is str and n and "\0" not in n for n in table):
        return None
    index = {n: i for i, n in enumerate(table)}
    name_idx = array.array('I', map(index.__getitem__, names_flat))
    names_blob = "\0".join(table).encode("utf-8")
    header = _CSR_HEADER.pack(_CSR_VERSION, len(names_blob), len(verts), len(weights))
    return b"".join((header, names_blob, _little_endian(verts).tobytes(),
                     _little_endian(lens).tobytes(), _little_endian(name_idx).tobytes(),
                     _little_endian(weights).tobytes()))


def _csr_arrays(payload):
    version, names_len, n_rows, nnz = _CSR_HEADER.unpack_from(payload, 0)
    if version != _CSR_VERSION:
        raise ValueError(f"unsupported layer blob version {version}")
    view = memoryview(payload)
    pos = _CSR_HEADER.size
    names = bytes(view[pos:pos + names_len]).decode("utf-8").split("\0") if names_len else []
    pos += names_len
    out = []
    for code, count in (('q', n_rows), ('I', n_rows), ('I', nnz), ('d', nnz)):
        arr = array.array(code)
        size = arr.itemsize * count
        if pos + size > len(payload):
            raise ValueError("truncated layer blob")
        arr.frombytes(view[pos:pos + size])
        out.append(_little_endian(arr))
        pos += size
    return (names, *out)


def _csr_to_dict(payload):
    names, verts, lens, name_idx, weights = _csr_arrays(payload)
    row_names = list(map(names.__getitem__, name_idx))
    row_weights = weights.tolist()
    layer = {}
    pos = 0
    for v, n in zip(verts.tolist(), lens.tolist()):
        end = pos + n
        layer[v] = dict(zip(row_names[pos:end], row_weights[pos:end]))
        pos = end
    return layer


def _payload(raw, magic):
    return zlib.decompress(base64.b64decode(raw[len(magic):]))


def decode_layer_dict(raw):
    if not raw or not isinstance(raw, str):
        return {}
    try:
        if raw.startswith(CSR_MAGIC):
            return _csr_to_dict(_payload(raw, CSR_MAGIC))
        if raw.startswith(MAGIC_STR):
            return pickle.loads(_payload(raw, MAGIC_STR))
    except Exception:
        return {}
    return {}


def layer_blob_arrays(raw):
    if not isinstance(raw, str) or not raw.startswith(CSR_MAGIC):
        return None
    try:
        return _csr_arrays(_payload(raw, CSR_MAGIC))
    except Exception:
        return None


def encode_layer_dict(layer_dict, *, fast=False):
    level = 0 if fast else 1
    payload = _encode_csr(layer_dict)
    if payload is not None:
        return CSR_MAGIC + base64.b64encode(zlib.compress(payload, level=level)).decode("ascii")
    raw = pickle.dumps(layer_dict, protocol=pickle.HIGHEST_PROTOCOL)
    return MAGIC_STR + base64.b64encode(zlib.compress(raw, level=level)).decode("ascii")


def encode_layer_arrays(names, vert_ids, row_lens, name_idx, weights, *, fast=False):
    names_blob = "\0".join(names).encode("utf-8")
    header = _CSR_HEADER.pack(_CSR_VERSION, len(names_blob), len(vert_ids), len(weights))
    payload = b"".join((
        header, names_blob,
        np.asarray(vert_ids).astype("<i8", copy=False).tobytes(),
        np.asarray(row_lens).astype("<u4", copy=False).tobytes(),
        np.asarray(name_idx).astype("<u4", copy=False).tobytes(),
        np.asarray(weights).astype("<f8", copy=False).tobytes(),
    ))
    return CSR_MAGIC + base64.b64encode(zlib.compress(payload, level=0 if fast else 1)).decode("ascii")


def layer_arrays_rows(arrays, verts):
    names, vert_ids, row_lens, name_idx, weights = arrays
    row_of = layer_arrays_row_index(arrays)
    starts = layer_arrays_starts(arrays).tolist()
    lens = np.asarray(row_lens).tolist()
    found = [(v, row_of[v]) for v in verts if v in row_of]
    out = {}
    if len(found) * 8 >= len(lens):
        all_names = list(map(names.__getitem__, np.asarray(name_idx).tolist()))
        all_weights = np.asarray(weights).tolist()
        for v, r in found:
            s = starts[r]
            e = s + lens[r]
            out[v] = dict(zip(all_names[s:e], all_weights[s:e]))
        return out
    for v, r in found:
        s = starts[r]
        e = s + lens[r]
        out[v] = dict(zip([names[i] for i in name_idx[s:e].tolist()], weights[s:e].tolist()))
    return out


def layer_arrays_entries(arrays, wanted):
    names, vert_ids, row_lens, name_idx, weights = arrays
    flags = np.fromiter((n in wanted for n in names), dtype=bool, count=len(names))
    if not flags.any():
        return {}
    name_idx = np.asarray(name_idx, dtype=np.int64)
    picked = flags[name_idx]
    rows = np.repeat(np.asarray(vert_ids, dtype=np.int64),
                     np.asarray(row_lens, dtype=np.int64))[picked].tolist()
    out = {}
    for v, i, w in zip(rows, name_idx[picked].tolist(), np.asarray(weights)[picked].tolist()):
        out.setdefault(v, {})[names[i]] = w
    return out


def layer_arrays_to_int(arrays, bone_to_id):
    names, vert_ids, row_lens, name_idx, weights = arrays
    ids = [bone_to_id.get(n) for n in names]
    idx_list = np.asarray(name_idx).tolist()
    all_ids = list(map(ids.__getitem__, idx_list))
    all_w = np.asarray(weights).tolist()
    verts = np.asarray(vert_ids).tolist()
    lens = np.asarray(row_lens).tolist()
    complete = None not in ids
    layer = {}
    pos = 0
    for v, n in zip(verts, lens):
        end = pos + n
        if complete:
            layer[v] = dict(zip(all_ids[pos:end], all_w[pos:end]))
        else:
            layer[v] = {b: w for b, w in zip(all_ids[pos:end], all_w[pos:end]) if b is not None}
        pos = end
    return layer


def layer_arrays_row_index(arrays):
    return dict(zip(np.asarray(arrays[1]).tolist(), range(len(arrays[1]))))


def layer_arrays_starts(arrays):
    lens = np.asarray(arrays[2], dtype=np.int64)
    return np.concatenate(([0], np.cumsum(lens)[:-1])) if len(lens) else lens


def replace_layer_rows(arrays, rows):
    names, vert_ids, row_lens, name_idx, weights = arrays
    vert_ids = np.asarray(vert_ids, dtype=np.int64)
    row_lens = np.asarray(row_lens, dtype=np.int64)
    name_idx = np.asarray(name_idx, dtype=np.int64)
    weights = np.asarray(weights, dtype=np.float64)
    table = list(names)
    name_pos = {n: i for i, n in enumerate(table)}
    row_of = dict(zip(vert_ids.tolist(), range(len(vert_ids))))

    new_lens = row_lens.copy()
    src_start = np.concatenate(([0], np.cumsum(row_lens)[:-1])) if len(row_lens) else row_lens.copy()
    keep = np.ones(len(vert_ids), dtype=bool)

    written = [(v, row) for v, row in rows.items() if row]
    for v, row in rows.items():
        if not row and v in row_of:
            keep[row_of[v]] = False
    pool_rows = [row for _v, row in written]
    pool_names = list(chain.from_iterable(pool_rows))
    for name in dict.fromkeys(pool_names):
        if name not in name_pos:
            name_pos[name] = len(table)
            table.append(name)
    pool_idx = np.fromiter(map(name_pos.__getitem__, pool_names), dtype=np.int64, count=len(pool_names))
    pool_w = np.fromiter(chain.from_iterable(map(dict.values, pool_rows)), dtype=np.float64,
                         count=len(pool_names))
    pool_lens = np.fromiter(map(len, pool_rows), dtype=np.int64, count=len(pool_rows))
    pool_starts = len(weights) + (np.concatenate(([0], np.cumsum(pool_lens)[:-1]))
                                  if len(pool_lens) else pool_lens)
    rows_at = np.fromiter((row_of.get(v, -1) for v, _row in written), dtype=np.int64,
                          count=len(written))
    existing = rows_at >= 0
    new_lens[rows_at[existing]] = pool_lens[existing]
    src_start[rows_at[existing]] = pool_starts[existing]
    appended_verts = [v for (v, _row), e in zip(written, existing.tolist()) if not e]
    appended_lens = pool_lens[~existing]
    appended_starts = pool_starts[~existing]

    all_idx = np.concatenate((name_idx, pool_idx))
    all_w = np.concatenate((weights, pool_w))
    out_verts = np.concatenate((vert_ids[keep], np.asarray(appended_verts, dtype=np.int64)))
    out_lens = np.concatenate((new_lens[keep], np.asarray(appended_lens, dtype=np.int64)))
    out_starts = np.concatenate((src_start[keep], np.asarray(appended_starts, dtype=np.int64)))
    total = int(out_lens.sum())
    first = np.repeat(out_starts - np.concatenate(([0], np.cumsum(out_lens)[:-1])), out_lens) \
        if len(out_lens) else np.zeros(0, dtype=np.int64)
    gather = first + np.arange(total, dtype=np.int64)
    out_idx = all_idx[gather]
    out_w = all_w[gather]

    used = np.unique(out_idx)
    if len(used) < len(table):
        remap = np.full(len(table), -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        out_idx = remap[out_idx]
        table = [table[i] for i in used.tolist()]
    return table, out_verts, out_lens, out_idx, out_w


def migrate_layer_blob(raw):
    if not isinstance(raw, str) or not raw.startswith(MAGIC_STR):
        return None
    old = decode_layer_dict(raw)
    if not old:
        return None
    new = encode_layer_dict(old)
    if not new.startswith(CSR_MAGIC) or decode_layer_dict(new) != old:
        return None
    return new


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
    arrays = layer_blob_arrays(raw)
    if arrays is not None and len(arrays[0]) and len(arrays[4]):
        names, verts, lens, name_idx, weights = arrays
        per_entry = np.repeat(np.frombuffer(verts, dtype=np.int64),
                              np.frombuffer(lens, dtype=np.uint32))
        vert_ids = array.array('I')
        vert_ids.frombytes(per_entry.astype(np.uint32).tobytes())
        bone_ids = array.array('i')
        bone_ids.frombytes(name_idx.tobytes())
        return vert_ids, bone_ids, weights, dict(enumerate(names))
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



def _visible_layers(meta_list, layer_data_map, mask_data_map):
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
        yield l_idx, raw_layer, mask_default, own_mask


_PAD_ROW = (array.array('I', [0xFFFFFFFF]), array.array('i', [0]), array.array('d', [0.0]))


@functools.lru_cache(maxsize=1)
def has_group_compositor() -> bool:
    return hasattr(RustWeightEngine("layer_compositor").module, "rust_composite_layer_groups")


def _composite_layer_groups(meta_list, layer_data_map, mask_data_map, name_to_idx, num_verts,
                            dirty_verts=None):
    unknown = {}

    def group_of(name):
        group = name_to_idx.get(name)
        if group is None:
            group = unknown.setdefault(name, -1 - len(unknown))
        return group

    mask_defaults, vert_cols, local_cols, weight_cols, id_maps, masks = [], [], [], [], [], []
    for _l_idx, raw_layer, mask_default, own_mask in _visible_layers(meta_list, layer_data_map,
                                                                      mask_data_map):
        if isinstance(raw_layer, tuple):
            vert_ids, local_ids, weights, id_to_name = raw_layer
        elif isinstance(raw_layer, dict):
            layer = {int(v): w for v, w in raw_layer.items()}
            bone_to_id, id_to_name = build_local_bone_ids(layer)
            vert_ids, local_ids, weights = layer_to_coo_sparse(layer, bone_to_id)
        else:
            vert_ids, local_ids, weights, id_to_name = _decode_and_coo_cached(raw_layer)
            if vert_ids is None:
                continue
        if not len(vert_ids):
            vert_ids, local_ids, weights = _PAD_ROW
        id_map = [0] * (max(id_to_name, default=-1) + 1)
        for local, name in id_to_name.items():
            id_map[local] = group_of(name)
        mask_defaults.append(mask_default)
        vert_cols.append(vert_ids)
        local_cols.append(local_ids)
        weight_cols.append(weights)
        id_maps.append(id_map)
        masks.append(own_mask)

    is_base = [i == 0 for i in range(len(mask_defaults))]
    args = [mask_defaults, is_base, vert_cols, local_cols, weight_cols, id_maps, masks, num_verts]
    if dirty_verts is not None:
        args.append(list(dirty_verts))
    return RustWeightEngine("layer_compositor").call("rust_composite_layer_groups", *args)


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

    for l_idx, raw_layer, mask_default, own_mask in _visible_layers(meta_list, layer_data_map,
                                                                     mask_data_map):
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
