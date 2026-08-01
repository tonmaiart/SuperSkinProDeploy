"""Layer data encoding/decoding and top-down compositing engine.

Serialisation pipeline: pickle -> zlib -> base64 -> "SSLY" header.
No bpy imports; operates on plain dict/str/float data.

Compositing: decodes/coerces layer and mask blobs into FFI-ready dicts,
then dispatches the actual top-down alpha-blend to the native Rust core
via RustWeightEngine (no Python fallback).
"""

import functools
import os
import pickle
import time
import zlib
import base64

from ..rust_weight_engine import RustWeightEngine
from ..rust_weight_engine.flat_array_bridge import build_local_bone_ids, layer_to_coo_sparse
from ..profiler import ProfilerService

# Temporary diagnostic split (harvest loop vs. the actual rust.call() FFI
# crossing) for the weight-apply gesture performance investigation -- opt-in
# via env var so it never affects normal use, and doesn't require importing
# DebugLogService (which pulls in bpy, violating this module's "no bpy
# imports" contract stated in the module docstring above). Remove once the
# harvest-vs-FFI split has been measured and the flat-array-FFI decision is
# made. ProfilerService (core_subsystems/profiler/) is safe to import here
# unlike DebugLogService -- it has no bpy dependency anywhere in its own
# package, so it doesn't violate this module's "no bpy imports" contract.
_PROFILE_COMPOSITOR = bool(os.environ.get("SSP_PROFILE_COMPOSITOR"))

# =========================================================================
#  Serialisation (formerly codec.py inside layer_manager/)
# =========================================================================

MAGIC_STR = "SSLY"


def decode_layer_dict(raw):
    """Decode a pickled layer blob -> ``{v_idx: {vg_idx: weight}}``.

    Returns ``{}`` on failure or when *raw* is falsy/not a string.
    """
    if not raw or not isinstance(raw, str) or not raw.startswith(MAGIC_STR):
        return {}
    try:
        compressed = base64.b64decode(raw[4:])
        return pickle.loads(zlib.decompress(compressed))
    except Exception:
        return {}


def _decode_and_intcast_layer_cached(raw):
    """Memoized decode + int-key-cast for a layer's raw blob, used ONLY by
    _composite_layers()'s own per-layer harvest loop below.

    Caches the FULL post-processing chain (decode_layer_dict() followed by
    the {int(v): w for v, w in decoded.items()} cast _composite_layers()
    always applied afterward) -- a decode-only cache still left that
    dict-rebuild running on every single tick against the (already-cached)
    decoded dict, which is itself O(layer size) for any non-active layer
    with substantial weight data. Caching the combined result makes a
    repeat call for the same raw string return the exact same dict object,
    doing no per-vertex work at all.

    decode_layer_dict() is a pure function of its string argument (no bpy
    import, no hidden state, deterministic unpickle), so caching is safe --
    but it's called from several other places across the codebase
    (storage_service.py, topology_heal.py, orphan_resolver.py, merge.py)
    that haven't all been audited for whether they ever mutate the returned
    dict in place. Rather than memoize the shared public function (and risk
    a stale/corrupted cache entry if any of those callers do mutate it),
    this wrapper is deliberately kept private and used only where it's
    already confirmed safe: _composite_layers() only ever reads the result,
    never mutates it in place.

    During a continuous modal gesture, non-active visible layers' raw blobs
    are byte-identical across every tick (only the active layer changes),
    so this turns repeated full unpickle/decompress/rebuild work into a
    cache hit. `maxsize=32` bounds memory to roughly a handful of distinct
    layer blob variants rather than growing unboundedly across a whole
    editing session.
    """
    decoded = decode_layer_dict(raw)
    if not decoded:
        return {}
    return {int(v): w for v, w in decoded.items()}


_decode_and_intcast_layer_cached = functools.lru_cache(maxsize=32)(_decode_and_intcast_layer_cached)


def _decode_and_normalize_mask_cached(raw):
    """Memoized decode + per-vertex-float-normalize for a layer's raw mask
    blob, used ONLY by _composite_layers()'s per-layer harvest loop.

    Same rationale as _decode_and_intcast_layer_cached above -- caches the
    full chain (decode + the mask-value normalization comprehension
    _composite_layers() always applies afterward), not just the decode.
    """
    mask_dict = decode_layer_dict(raw)
    if not mask_dict:
        return {}
    return {
        int(v): (next(iter(w.values()), 0.0) if isinstance(w, dict) else float(w))
        for v, w in mask_dict.items()
    }


_decode_and_normalize_mask_cached = functools.lru_cache(maxsize=32)(_decode_and_normalize_mask_cached)


def _decode_and_coo_cached(raw):
    """Memoized decode + local-bone-id-mapping + COO-flattening for a
    layer's raw blob, used ONLY by _composite_layers()'s harvest loop when
    the flat-array FFI path (rust_composite_layers_mixed) is available.

    Same caching rationale as _decode_and_intcast_layer_cached above -- the
    decoded/flattened result is only ever read by the harvest loop, never
    mutated in place, and non-active visible layers' raw blobs stay
    byte-identical across every tick of a continuous modal gesture.

    Returns:
        (vert_ids, bone_ids, weights, id_to_bone) tuple. When the layer
        decodes to nothing, returns (None, None, None, None) -- callers
        must check for this sentinel explicitly, since an empty-but-valid
        layer (arrays of length 0) is a legitimate, different outcome.
    """
    decoded = decode_layer_dict(raw)
    if not decoded:
        return None, None, None, None
    layer_int = {int(v): w for v, w in decoded.items()}
    bone_to_id, id_to_bone = build_local_bone_ids(layer_int)
    vert_ids, bone_ids, weights = layer_to_coo_sparse(layer_int, bone_to_id)
    return vert_ids, bone_ids, weights, id_to_bone


_decode_and_coo_cached = functools.lru_cache(maxsize=32)(_decode_and_coo_cached)


def encode_layer_dict(layer_dict):
    """Encode a ``{v_idx: {vg_idx: weight}}`` dict -> pickled blob string."""
    raw = pickle.dumps(layer_dict, protocol=pickle.HIGHEST_PROTOCOL)
    compressed = zlib.compress(raw, level=1)
    return MAGIC_STR + base64.b64encode(compressed).decode("ascii")


def mask_value(raw):
    """Safely extract a ``float`` mask value from either storage format.

    New format: *raw* is a plain ``float`` (0.0-1.0).
    Legacy format: *raw* is ``{vg_idx: weight}`` -> first value extracted.

    Returns ``1.0`` for falsy inputs (missing mask -> fully visible).
    """
    if isinstance(raw, dict):
        return next(iter(raw.values()), 1.0) if raw else 1.0
    if isinstance(raw, (int, float)):
        return float(raw)
    return 1.0


# =========================================================================
#  Layer Compositing (formerly composite_layers in compositor.py)
# =========================================================================

def _composite_layers(meta_list, layer_data_map, mask_data_map, idx_to_name, num_verts,
                      dirty_verts=None):
    """Top-down alpha-blend all visible layers via the native Rust core.

    This is the private implementation function; external callers must use
    LayerCompositor.composite_layers() which delegates here.

    Args:
        dirty_verts: optional iterable of vertex indices. When given, only
            these vertices are (re)computed by the Rust engine instead of
            every vertex in the mesh -- for hot paths (e.g. a modal
            weight-painting gesture) that know exactly which vertices could
            possibly have changed. `None` (default) preserves full-mesh
            compositing exactly. Requires a `rust_logic` build with
            `dirty_verts` support in `rust_composite_layers` -- passing a
            non-None value against an older binary will raise a TypeError
            from the FFI call, surfaced as RustUnavailableError by the
            gateway.
    """
    # Combined gate -- see core/ui_controller/pipeline.py's
    # flatten_to_mesh_edit() for why both consumers share one set of
    # timestamps instead of each computing their own.
    _profile_metrics = ProfilerService.is_enabled()
    _profile = _PROFILE_COMPOSITOR or _profile_metrics
    _t0 = time.perf_counter() if _profile else None

    rust = RustWeightEngine("layer_compositor")
    # rust_composite_layers_mixed is a brand-new export that only exists
    # after a human rebuild of rust_logic.so (docs/bug-history/0012 --
    # the build toolchain is intentionally outside an AI agent's reach).
    # Detecting availability here, once per call, lets every existing
    # caller (Save Weight, layer merge, the weight-apply gesture) keep
    # working unchanged against an un-rebuilt binary and switch to the
    # flat-array (COO) path automatically the moment the rebuild lands --
    # no further code changes needed, no risk of calling a symbol that
    # does not exist yet.
    use_mixed = hasattr(rust.module, "rust_composite_layers_mixed")

    meta_clean = []
    mask_decoded_map = {}
    ordered_meta = reversed(meta_list)
    foundation_seen = False

    if use_mixed:
        dict_layer_data_map = {}
        coo_vert_ids_map = {}
        coo_bone_ids_map = {}
        coo_weights_map = {}
        coo_id_to_bone_map = {}
    else:
        layer_decoded_map = {}

    for layer in ordered_meta:
        if not layer.get("visible", True):
            continue
        l_idx = int(layer["index"])
        raw_layer = layer_data_map.get(l_idx)
        if not raw_layer:
            continue
        # A hot-path caller (the weight-apply gesture, via
        # flatten_to_mesh_edit()'s active_layer_override) may hand the
        # active layer's data through as an already-decoded dict instead of
        # an encoded blob string, to skip a pointless pickle/zlib/base64
        # round-trip of data that's already in memory in exactly this
        # shape. This is always small (dirty_verts-sized) and always kept
        # as a plain dict regardless of use_mixed -- not worth flattening
        # to COO for a handful of entries.
        if isinstance(raw_layer, dict):
            if not raw_layer:
                continue
            layer_int = {int(v): w for v, w in raw_layer.items()}
            if use_mixed:
                dict_layer_data_map[l_idx] = layer_int
            else:
                layer_decoded_map[l_idx] = layer_int
        elif use_mixed:
            vert_ids, bone_ids, weights, id_to_bone = _decode_and_coo_cached(raw_layer)
            if vert_ids is None:
                continue
            coo_vert_ids_map[l_idx] = vert_ids
            coo_bone_ids_map[l_idx] = bone_ids
            coo_weights_map[l_idx] = weights
            coo_id_to_bone_map[l_idx] = id_to_bone
        else:
            cached = _decode_and_intcast_layer_cached(raw_layer)
            if not cached:
                continue
            layer_decoded_map[l_idx] = cached

        meta_clean.append({
            "index": float(l_idx),
            "mask_default": float(layer.get("mask_default", 1.0)),
            "is_base": 1.0 if not foundation_seen else 0.0,
        })
        foundation_seen = True

        raw_mask = mask_data_map.get(l_idx)
        if raw_mask:
            if isinstance(raw_mask, dict):
                mask_decoded_map[l_idx] = {
                    int(v): (next(iter(w.values()), 0.0) if isinstance(w, dict) else float(w))
                    for v, w in raw_mask.items()
                }
            else:
                mask_decoded_map[l_idx] = _decode_and_normalize_mask_cached(raw_mask)
        else:
            mask_decoded_map[l_idx] = {}

    if _profile:
        _t_harvest = time.perf_counter()

    if use_mixed:
        if dirty_verts is None:
            result = rust.call(
                "rust_composite_layers_mixed", meta_clean, dict_layer_data_map,
                coo_vert_ids_map, coo_bone_ids_map, coo_weights_map, coo_id_to_bone_map,
                mask_decoded_map, num_verts,
            )
        else:
            result = rust.call(
                "rust_composite_layers_mixed", meta_clean, dict_layer_data_map,
                coo_vert_ids_map, coo_bone_ids_map, coo_weights_map, coo_id_to_bone_map,
                mask_decoded_map, num_verts, list(dirty_verts),
            )
    elif dirty_verts is None:
        # Unconditional 4-arg call, byte-identical to before this parameter
        # existed -- every caller outside the weight-apply gesture (Save
        # Weight, layer merge, etc.) still takes this path. rust_logic.so
        # has been rebuilt with dirty_verts support in rust_composite_layers
        # (rust_logic/src/lib.rs) and core/ui_controller/pipeline.py's
        # flatten_to_mesh_edit() does pass a real dirty_verts through here
        # for the gesture's hot path -- this branch only covers the
        # `None` case, not "nobody uses it".
        result = rust.call(
            "rust_composite_layers", meta_clean, layer_decoded_map, mask_decoded_map, num_verts,
        )
    else:
        result = rust.call(
            "rust_composite_layers", meta_clean, layer_decoded_map, mask_decoded_map,
            num_verts, list(dirty_verts),
        )

    if _profile:
        _t_rust = time.perf_counter()
        if _PROFILE_COMPOSITOR:
            print(
                f"[SSP:COMPOSITOR_PROFILE] layers={len(meta_clean)} mixed={use_mixed} "
                f"harvest={1000 * (_t_harvest - _t0):.2f}ms "
                f"rust_call={1000 * (_t_rust - _t_harvest):.2f}ms "
                f"total={1000 * (_t_rust - _t0):.2f}ms"
            )
        if _profile_metrics:
            _base = "core_subsystems.layer_compositor.composite_layers"
            _size = len(dirty_verts) if dirty_verts is not None else num_verts
            ProfilerService.record(f"{_base}.harvest", 1000 * (_t_harvest - _t0), _size)
            ProfilerService.record(f"{_base}.rust_call", 1000 * (_t_rust - _t_harvest), _size)
            ProfilerService.record(f"{_base}.total", 1000 * (_t_rust - _t0), _size)

    return result
