"""Flat-array bridge for zero-copy Python <-> Rust FFI data transfer.

Converts between SuperSkinPro's storage-native nested-dict format
(``{v_idx: {bone_name: weight}}``) and Compressed Sparse Row (CSR) flat
arrays that cross the PyO3 FFI boundary with minimal allocation overhead.

CSR Layout (for layer weights -- per-vertex, per-bone):
    vertex_offsets:  ``[u32]``   length = num_verts + 1
    bone_ids:        ``[i32]``   length = total_weight_entries
    weights:         ``[f64]``   length = total_weight_entries

Flat Layout (for mask weights -- per-vertex, single float):
    mask_weights:    ``[f64]``   length = num_verts
                                 sentinel = -1.0 means "no explicit value"

Why CSR?  On a 100k-vert mesh with ~4 bones per vertex, the nested-dict
approach allocates ~400,000 Python dict key/value pairs crossing the FFI.
CSR passes three contiguous arrays with zero per-element Python-object
overhead -- PyO3 converts them as native ``Vec<T>`` slices.

Architecture Note:
    Storage format (String bone-name keys) is NEVER altered.  This module
    operates on the *already-mapped* Integer bone-ID format (vertex-group
    indices) produced by ``LayerStorageService.get_local_mapping()``.
"""

from __future__ import annotations

import array
from typing import Sequence, Tuple

# =========================================================================
#  Sentinel constants
# =========================================================================

MASK_SENTINEL: float = -1.0
"""Sentinel value in flat mask arrays meaning *no explicit weight stored*."""


# =========================================================================
#  Layer-dict <-> CSR
# =========================================================================

def layer_to_csr(
    layer_int: dict,
    num_verts: int,
    *,
    dtype_offsets: str = "I",   # unsigned int (u32)
    dtype_ids: str = "i",       # signed int (i32)
    dtype_weights: str = "d",   # double (f64)
) -> Tuple[array.array, array.array, array.array]:
    """Convert ``{v_idx: {bone_id: weight}}`` to CSR flat arrays.

    Args:
        layer_int: Int-keyed layer dict ``{v_idx: {bone_id: weight, ...}, ...}``.
        num_verts: Total vertex count in the mesh (determines offset array size).

    Returns:
        ``(vertex_offsets, bone_ids, weights)`` as Python ``array.array``
        objects backed by contiguous C buffers -- ready for zero-copy FFI.

    Example:
        >>> layer_to_csr({0: {0: 0.5, 1: 0.5}, 2: {0: 1.0}}, num_verts=3)
        (array('I', [0, 2, 2, 3]), array('i', [0, 1, 0]), array('d', [0.5, 0.5, 1.0]))
    """
    # Phase 1 -- count total entries for pre-allocation
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
    """Build a call-scoped ``bone_name <-> int_id`` mapping from the distinct
    bone names appearing in one decoded layer dict.

    Deliberately NOT borrowed from any object-wide mapping (e.g.
    ``get_unified_mapping()``/``get_local_mapping()``) -- those are keyed by
    the live ``vertex_groups`` collection and are unstable across VG
    add/remove, and some callers (layer merge) can reference orphan bone
    names with no current VG at all. This mapping only needs to be
    self-consistent for the lifetime of a single caller's use of it (e.g.
    one compositor call), covering exactly whatever bone names are actually
    present in *this* layer dict -- including orphans, since it derives
    from the data itself, not from live scene state.

    Args:
        layer_int: ``{v_idx: {bone_name: weight}}`` -- a single decoded layer.

    Returns:
        ``(bone_to_id, id_to_bone)`` -- ``id_to_bone`` keys are small
        sequential ints starting at 0 (typically tens of entries, one per
        distinct bone name in this layer).
    """
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
    dtype_vert_ids: str = "I",  # unsigned int (u32)
    dtype_bone_ids: str = "i",  # signed int (i32)
    dtype_weights: str = "d",   # double (f64)
) -> Tuple[array.array, array.array, array.array]:
    """Convert ``{v_idx: {bone_name: weight}}`` to flat COO (coordinate-list)
    arrays, using a caller-supplied LOCAL ``bone_to_id`` mapping (see
    ``build_local_bone_ids()``) instead of object-wide vertex-group indices.

    Unlike ``layer_to_csr()`` above (which assumes a dense ``0..num_verts``
    range and is O(num_verts) regardless of how sparse the layer actually
    is), this iterates only ``layer_int``'s own keys -- O(actual entries),
    safe and efficient for a layer that only touches a small fraction of a
    large mesh. There is no ``vertex_offsets`` array in this format: instead,
    ``vert_ids[i]`` names which vertex ``bone_ids[i]``/``weights[i]`` belong
    to, with each vertex's bone count contributing that many rows (a vertex
    with zero entries in ``layer_int`` simply never appears in ``vert_ids``
    at all -- this "absence means no entries" contract must be preserved
    exactly by whatever reconstructs the nested dict on the Rust side; see
    ``coo_layer_to_hashmap()`` in ``rust_logic/src/layer_compositor.rs``).

    Args:
        layer_int: ``{v_idx: {bone_name: weight}}`` -- a single decoded layer.
        bone_to_id: ``{bone_name: int_id}``, from ``build_local_bone_ids()``
            on this SAME ``layer_int`` -- using a mapping built from a
            different layer's bone names would silently drop or
            misattribute entries.

    Returns:
        ``(vert_ids, bone_ids, weights)`` as parallel ``array.array``
        objects, all the same length (total vertex-bone weight entries in
        ``layer_int``).
    """
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
        # A bone name absent from bone_to_id was skipped -- shrink the
        # arrays to the actual entry count rather than leaving trailing
        # zeroed rows (which would silently fabricate a fake vertex-0 /
        # bone-0 / weight-0.0 entry that was never in the source data).
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
    """Convert CSR flat arrays back to ``{v_idx: {bone_id: weight}}``.

    Args:
        vertex_offsets: CSR offset array (length = num_verts + 1).
        bone_ids: Flat bone-ID array.
        weights: Flat weight-value array.
        num_verts: Vertex count.

    Returns:
        Int-keyed layer dict suitable for ``map_layer_to_string()``.
    """
    result: dict = {}
    total = len(bone_ids)
    for v_idx in range(num_verts):
        if v_idx >= len(vertex_offsets):
            break
        start = int(vertex_offsets[v_idx])
        end = int(vertex_offsets[v_idx + 1]) if (v_idx + 1) < len(vertex_offsets) else total
        # Clamp to valid array bounds (defensive -- guards against malformed
        # CSR arrays returned by a Rust function or edge-case offset values).
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


# =========================================================================
#  Mask-dict <-> flat array
# =========================================================================

def mask_to_flat(
    mask_dict: dict,
    num_verts: int,
    *,
    dtype: str = "d",
    sentinel: float = MASK_SENTINEL,
) -> array.array:
    """Convert ``{v_idx: float}`` mask dict to a dense flat array.

    Vertices with no stored mask value receive *sentinel* (default: -1.0).

    Args:
        mask_dict: Int-keyed mask dict (already normalised by storage service).
        num_verts: Total vertex count.
        sentinel: Value for vertices with no explicit mask entry.

    Returns:
        Flat ``array('d', ...)`` of length *num_verts*.
    """
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
    """Convert flat mask array back to ``{v_idx: float}`` dict.

    Entries equal to *sentinel* are omitted from the result.
    """
    result: dict = {}
    for v_idx, w in enumerate(mask_flat):
        fw = float(w)
        if fw != sentinel:
            result[v_idx] = fw
    return result


# =========================================================================
#  Bulk deformed-coordinate extraction (NumPy / foreach_get helper)
# =========================================================================

def extract_deformed_coords(obj_eval, num_verts: int):
    """Extract deformed world-space vertex coordinates using Blender's
    fast C-extension ``foreach_get``, returning a flat ``array('d', ...)``
    of length ``num_verts * 3`` (interleaved x, y, z).

    Uses ``obj_eval.to_mesh()`` to get the evaluated (deformed) mesh,
    then calls ``foreach_get('co', buffer)`` on the vertex attribute.
    Falls back to returning None on error.

    Args:
        obj_eval: The evaluated object from ``obj.evaluated_get(depsgraph)``.
        num_verts: Expected vertex count (from edit-bmesh for validation).

    Returns:
        ``array('d', ...)`` of length ``num_verts * 3`` -- interleaved
        float coordinates, or ``None`` on failure.
    """
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
