
from __future__ import annotations

import array
from itertools import chain
from operator import methodcaller
from typing import Sequence, Tuple

import numpy as np


MASK_SENTINEL: float = -1.0
"""Sentinel value in flat mask arrays meaning *no explicit weight stored*."""

_EMPTY_COO_PAD_VERT_ID: int = -1
"""Padding vertex id for a would-be zero-length COO row (see `int_layer_to_coo`)."""



def layer_to_csr(
    layer_int: dict,
    num_verts: int,
    *,
    dtype_offsets: str = "I",
    dtype_ids: str = "i",
    dtype_weights: str = "d",
) -> Tuple[array.array, array.array, array.array]:
    total_entries = sum(len(layer_int.get(v, {})) for v in range(num_verts))

    offsets = array.array(dtype_offsets, [0]) * (num_verts + 1)
    bone_ids = array.array(dtype_ids, [0]) * total_entries
    weights = array.array(dtype_weights, [0.0]) * total_entries

    cursor = 0
    for v_idx in range(num_verts):
        offsets[v_idx] = cursor
        vw = layer_int.get(v_idx, {})
        if isinstance(vw, dict):
            for b_id, w in vw.items():
                bone_ids[cursor] = int(b_id)
                weights[cursor] = float(w)
                cursor += 1
    offsets[num_verts] = cursor

    return offsets, bone_ids, weights


def build_local_bone_ids(layer_int: dict) -> Tuple[dict, dict]:
    names = set()
    for bone_weights in layer_int.values():
        names.update(bone_weights.keys())
    id_to_bone = dict(enumerate(sorted(names)))
    bone_to_id = {name: i for i, name in id_to_bone.items()}
    return bone_to_id, id_to_bone


def layer_to_coo_sparse(
    layer_int: dict,
    bone_to_id: dict,
    *,
    dtype_vert_ids: str = "I",
    dtype_bone_ids: str = "i",
    dtype_weights: str = "d",
) -> Tuple[array.array, array.array, array.array]:
    total_entries = sum(len(w) for w in layer_int.values())

    vert_ids = array.array(dtype_vert_ids, [0]) * total_entries
    bone_ids = array.array(dtype_bone_ids, [0]) * total_entries
    weights = array.array(dtype_weights, [0.0]) * total_entries

    cursor = 0
    for v_idx, bone_weights in layer_int.items():
        v_int = int(v_idx)
        for bone_name, w in bone_weights.items():
            b_id = bone_to_id.get(bone_name)
            if b_id is None:
                continue
            vert_ids[cursor] = v_int
            bone_ids[cursor] = b_id
            weights[cursor] = float(w)
            cursor += 1

    if cursor != total_entries:
        vert_ids = vert_ids[:cursor]
        bone_ids = bone_ids[:cursor]
        weights = weights[:cursor]

    return vert_ids, bone_ids, weights


def csr_to_layer(
    vertex_offsets: Sequence[int],
    bone_ids: Sequence[int],
    weights: Sequence[float],
    num_verts: int,
) -> dict:
    result: dict = {}
    total = len(bone_ids)
    for v_idx in range(num_verts):
        if v_idx >= len(vertex_offsets):
            break
        start = int(vertex_offsets[v_idx])
        end = int(vertex_offsets[v_idx + 1]) if (v_idx + 1) < len(vertex_offsets) else total
        start = max(0, min(start, total))
        end = max(start, min(end, total))
        if start >= end:
            continue
        inner: dict = {}
        for i in range(start, end):
            inner[int(bone_ids[i])] = float(weights[i])
        if inner:
            result[v_idx] = inner
    return result



def int_layer_to_coo(
    layer_int: dict,
    *,
    dtype_vert_ids: str = "q",
    dtype_bone_ids: str = "i",
    dtype_weights: str = "d",
) -> Tuple[array.array, array.array, array.array]:
    vert_ids, bone_ids, weights = int_layer_columns(layer_int, np.dtype(dtype_bone_ids),
                                                    np.dtype(dtype_weights))
    if not len(vert_ids):
        return (array.array(dtype_vert_ids, [_EMPTY_COO_PAD_VERT_ID]),
                array.array(dtype_bone_ids, [0]), array.array(dtype_weights, [0.0]))
    return (_to_array(dtype_vert_ids, vert_ids), _to_array(dtype_bone_ids, bone_ids),
            _to_array(dtype_weights, weights))


def int_layer_columns(layer: dict, key_dtype=np.int64, weight_dtype=np.float64) -> tuple:
    columns = getattr(layer, "columns", None)
    if columns is not None:
        return columns(key_dtype, weight_dtype)
    rows = layer.values()
    lens = np.fromiter(map(len, rows), dtype=np.int64, count=len(layer))
    total = int(lens.sum())
    vert_ids = np.repeat(np.fromiter(layer, dtype=np.int64, count=len(layer)), lens)
    keys = np.fromiter(chain.from_iterable(rows), dtype=key_dtype, count=total)
    weights = np.fromiter(chain.from_iterable(map(methodcaller("values"), rows)),
                          dtype=weight_dtype, count=total)
    return vert_ids, keys, weights


def _to_array(typecode: str, values: np.ndarray) -> array.array:
    out = array.array(typecode)
    out.frombytes(values.astype(np.dtype(typecode), copy=False).tobytes())
    return out


def coo_to_int_layer(
    vert_ids: Sequence[int],
    bone_ids: Sequence[int],
    weights: Sequence[float],
) -> dict:
    result: dict = {}
    for v, b, w in zip(vert_ids, bone_ids, weights):
        v_int = int(v)
        if v_int < 0:
            continue
        result.setdefault(v_int, {})[int(b)] = float(w)
    return result



def mask_to_flat(
    mask_dict: dict,
    num_verts: int,
    *,
    dtype: str = "d",
    sentinel: float = MASK_SENTINEL,
) -> array.array:
    flat = array.array(dtype, [sentinel]) * num_verts
    for v_idx, w in mask_dict.items():
        v_int = int(v_idx)
        if 0 <= v_int < num_verts:
            flat[v_int] = float(w)
    return flat


def flat_to_mask(
    mask_flat: Sequence[float],
    *,
    sentinel: float = MASK_SENTINEL,
) -> dict:
    result: dict = {}
    for v_idx, w in enumerate(mask_flat):
        fw = float(w)
        if fw != sentinel:
            result[v_idx] = fw
    return result



def extract_deformed_coords(obj_eval, num_verts: int):
    import bpy

    coords_flat = None
    try:
        eval_mesh: bpy.types.Mesh = obj_eval.to_mesh()
        if eval_mesh and len(eval_mesh.vertices) == num_verts:
            coords_flat = array.array("d", [0.0]) * (num_verts * 3)
            eval_mesh.vertices.foreach_get("co", coords_flat)
    except Exception:
        coords_flat = None
    finally:
        obj_eval.to_mesh_clear()

    return coords_flat
