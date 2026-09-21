"""Temporary Vertex Group bridge -- the paint surface of a Weight Paint session.

While the Edit Layer Weight session runs in Weight Paint Mode, the active
Layer's own set of __ssp_* vertex groups is what native brushes paint on.
The session state is pulled back into permanent storage (ss_layer_N /
ss_mask_N) at sync points, and ss_layer_N feeds the compositor that writes
the real deform vertex groups.

Naming convention (per-layer namespaced, index-based to avoid the 64-char
bone-name limit):
    __ssp_L{layer_idx}_{vg_idx}  -> bone at vg_index, for Layer layer_idx
    __ssp_L{layer_idx}_m         -> mask weights, for Layer layer_idx
    __ssp_meta   -> session marker VG (no vertex weights of its own).
                    Payload lives in object custom props:
                      "__ssp_meta_layer" = int, the CURRENTLY ACTIVE layer index
                      "__ssp_meta_map"   = JSON str "{vg_index: bone_name}"
                                           (a property of the mesh's armature,
                                           not of any one Layer)

Functions that create or remove vertex groups must run in Object or Weight
Paint Mode.
"""

import json

PREFIX = "__ssp_"
_LAYER_PREFIX = "__ssp_L"
META_VG_NAME = "__ssp_meta"

_LEGACY_META_ATTRIBUTES = ("__ssp_meta_list_len",) + tuple(
    f"__ssp_meta_list_chunk_{i}" for i in range(10)
)


def has_temp_vgs(obj) -> bool:
    """Return True if obj currently has a live paint session at all."""
    return any(vg.name == META_VG_NAME for vg in obj.vertex_groups)


def has_layer_cache(obj, layer_idx: int) -> bool:
    """Return True if this specific Layer has its own resident temp-VG set."""
    return obj.vertex_groups.get(mask_vg_name(layer_idx)) is not None


def weight_vg_name(layer_idx: int, vg_idx: int) -> str:
    """Name of the per-Layer, per-bone temp VG for Layer *layer_idx*'s bone
    at unified vg_index *vg_idx*."""
    return f"{_LAYER_PREFIX}{layer_idx}_{vg_idx}"


def mask_vg_name(layer_idx: int) -> str:
    """Name of the per-Layer mask temp VG for Layer *layer_idx*. Also the
    existence check behind has_layer_cache() -- always created for a Layer's
    temp-VG set even when its mask is empty."""
    return f"{_LAYER_PREFIX}{layer_idx}_m"


def parse_layer_weight_vg_name(vg_name: str):
    """Parse '__ssp_L{layer_idx}_{vg_idx}' -> (layer_idx, vg_idx).

    Returns None if vg_name isn't a per-Layer WEIGHT VG (a mask VG's suffix
    is the non-numeric "m", so it returns None here too)."""
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
    """Drop the dense per-vertex metadata layers that earlier releases left
    on the mesh permanently; nothing reads them any more."""
    for name in _LEGACY_META_ATTRIBUTES:
        attr = mesh.attributes.get(name)
        if attr is None:
            continue
        try:
            mesh.attributes.remove(attr)
        except RuntimeError:
            pass


def layer_mask_default(storage, layer_idx: int) -> float:
    """Mask value stored-mask gaps stand for on Layer *layer_idx*."""
    for layer in storage.read_meta_list():
        if layer.get("index") == layer_idx:
            return float(layer.get("mask_default", 1.0))
    return 1.0


def _strip_mask_default(mask_dict: dict, mask_default: float) -> dict:
    """Drop entries equal to the sparse-storage default, so a fully
    materialized temp mask VG round-trips to the same sparse dict."""
    if mask_default <= 0.0:
        return mask_dict
    return {v: w for v, w in mask_dict.items() if not _mask_values_close(float(w), mask_default)}


def load_layer_to_temp_vgs(obj, layer_dict: dict, mask_dict: dict,
                           layer_idx: int, id_to_bone: dict,
                           mask_default: float = 0.0):
    """Create Layer *layer_idx*'s own temp-VG set from layer_dict/mask_dict
    and mark it the active Layer. Any prior set for the same Layer is
    replaced; other Layers' sets are left alone.

    Args:
        layer_dict: {v_idx: {bone_name: weight}} -- string-keyed (storage format)
        mask_dict: {v_idx: float}; vertices absent from it get *mask_default*
        id_to_bone: {vg_index: bone_name}, shared across every Layer
    """
    bone_to_vg_index = {name: idx for idx, name in id_to_bone.items()}

    delete_temp_vgs(obj, layer_idx=layer_idx)

    # A VG for every bone, weighted or not, so apply_active_bone() can always
    # locate this Layer's VG for any bone.
    vg_index_to_temp_name = {}
    for vg_idx, _bone_name in id_to_bone.items():
        temp_name = weight_vg_name(layer_idx, vg_idx)
        obj.vertex_groups.new(name=temp_name)
        vg_index_to_temp_name[vg_idx] = temp_name

    bone_vertex_weights = {}
    for v_idx, v_weights in layer_dict.items():
        v_int = int(v_idx)
        for bone_name, weight in v_weights.items():
            vg_idx = bone_to_vg_index.get(bone_name)
            if vg_idx is None or vg_idx not in vg_index_to_temp_name:
                continue
            bone_vertex_weights.setdefault(vg_idx, []).append((v_int, float(weight)))

    for vg_idx, entries in bone_vertex_weights.items():
        vg = obj.vertex_groups.get(vg_index_to_temp_name[vg_idx])
        if vg is None:
            continue
        for v_int, weight in entries:
            vg.add([v_int], weight, 'REPLACE')

    mask_vg = obj.vertex_groups.new(name=mask_vg_name(layer_idx))
    if mask_default > 0.0:
        present = {int(v) for v in mask_dict}
        missing = [v for v in range(len(obj.data.vertices)) if v not in present]
        if missing:
            mask_vg.add(missing, float(mask_default), 'REPLACE')
    for v_idx, weight in mask_dict.items():
        mask_vg.add([int(v_idx)], float(weight), 'REPLACE')

    if obj.vertex_groups.get(META_VG_NAME) is None:
        obj.vertex_groups.new(name=META_VG_NAME)
        _purge_legacy_meta_attributes(obj.data)
    obj["__ssp_meta_layer"] = layer_idx
    obj["__ssp_meta_map"] = json.dumps({str(k): v for k, v in id_to_bone.items()})


def read_temp_vgs_to_layer(obj, layer_idx: int = None) -> tuple:
    """Read one Layer's temp VGs back into layer_dict and mask_dict.

    Args:
        layer_idx: which Layer's __ssp_L{layer_idx}_* set to read. None
            resolves to the currently active Layer (obj["__ssp_meta_layer"]).

    Returns:
        (layer_dict, mask_dict, active_layer_index)
    """
    meta_vg = obj.vertex_groups.get(META_VG_NAME)
    if meta_vg is None:
        return {}, {}, 0

    active_layer_index = int(obj.get("__ssp_meta_layer", 0))
    if layer_idx is None:
        layer_idx = active_layer_index

    map_raw = obj.get("__ssp_meta_map", "{}")
    try:
        id_to_bone = {int(k): v for k, v in json.loads(map_raw).items()}
    except Exception:
        id_to_bone = {}

    mesh = obj.data

    layer_dict = {}
    for vg in obj.vertex_groups:
        parsed = parse_layer_weight_vg_name(vg.name)
        if parsed is None or parsed[0] != layer_idx:
            continue
        bone_name = id_to_bone.get(parsed[1])
        if bone_name is None:
            continue
        for v in mesh.vertices:
            for g in v.groups:
                if g.group == vg.index and g.weight > 0.0:
                    layer_dict.setdefault(v.index, {})[bone_name] = g.weight

    mask_dict = {}
    mask_vg = obj.vertex_groups.get(mask_vg_name(layer_idx))
    if mask_vg:
        for v in mesh.vertices:
            for g in v.groups:
                # An explicit 0.0 mask value means fully masked out, so unlike
                # a zero bone weight it must be kept.
                if g.group == mask_vg.index:
                    mask_dict[v.index] = g.weight

    _prune_zero_weights(layer_dict)

    return layer_dict, mask_dict, active_layer_index


def _prune_zero_weights(layer_dict: dict):
    """Remove bone entries with zero weight across all vertices in-place, so
    orphan bones scaled to 0 by weight ops don't survive into storage as
    zero-weight noise."""
    all_bones: set = set()
    for v_weights in layer_dict.values():
        all_bones.update(v_weights.keys())

    zero_bones = {
        bone_name for bone_name in all_bones
        if all(v_weights.get(bone_name, 0.0) <= 0.0001
               for v_weights in layer_dict.values())
    }

    if not zero_bones:
        return

    for v_weights in layer_dict.values():
        for bone_name in zero_bones:
            v_weights.pop(bone_name, None)

    empty_verts = [v for v, w in layer_dict.items() if not w]
    for v in empty_verts:
        del layer_dict[v]


def delete_temp_vgs(obj, layer_idx: int = None):
    """Remove temp VGs.

    layer_idx=None: wipe every __ssp_* VG plus the __ssp_meta_* custom props
    (session teardown).

    layer_idx=<int>: wipe only that Layer's own weight/mask VGs, leaving the
    session marker in place.
    """
    if layer_idx is None:
        to_remove = [vg for vg in obj.vertex_groups if vg.name.startswith(PREFIX)]
        for vg in to_remove:
            obj.vertex_groups.remove(vg)
        obj.pop("__ssp_meta_layer", None)
        obj.pop("__ssp_meta_map", None)
    else:
        prefix = f"{_LAYER_PREFIX}{layer_idx}_"
        to_remove = [vg for vg in obj.vertex_groups if vg.name.startswith(prefix)]
        for vg in to_remove:
            obj.vertex_groups.remove(vg)


def get_active_layer_from_meta(obj) -> int:
    """Read active layer index from object metadata props. Returns -1 if not found."""
    if not has_temp_vgs(obj):
        return -1
    return int(obj.get("__ssp_meta_layer", -1))


def _mask_values_close(value: float, target: float) -> bool:
    return abs(value - target) < 1e-4


def get_layer_mask_state(obj, storage, layer_index: int) -> str:
    """Classify *layer_index*'s raw per-vertex mask coverage across the whole
    mesh: ``'WHITE'`` (every vertex 1.0), ``'BLACK'`` (every vertex 0.0), or
    ``'EDITED'`` (anything else). This is the layer's own raw mask, not the
    "cut" result after compositing against sibling layers.

    Reads permanent ``ss_mask_N`` storage; native strokes reach it through
    pull_temp_to_storage(). Backs the Layer list's per-row mask-state icon."""
    meta = storage.read_meta_list()
    layer_meta = next((layer for layer in meta if layer.get("index") == layer_index), None)
    if layer_meta is None:
        return 'EDITED'
    mask_default = float(layer_meta.get("mask_default", 1.0))

    mask_dict = storage.read_mask_dict(layer_index)

    num_verts = len(obj.data.vertices)
    values = set(mask_dict.values())
    if len(mask_dict) < num_verts:
        values.add(mask_default)

    if all(_mask_values_close(v, 1.0) for v in values):
        return 'WHITE'
    if all(_mask_values_close(v, 0.0) for v in values):
        return 'BLACK'
    return 'EDITED'


# ── Weight Paint session: temp VGs read/written through plain mesh data ──────

_WP_EPSILON = 1e-5


def is_wp_session(obj) -> bool:
    return obj is not None and obj.mode == 'WEIGHT_PAINT' and has_temp_vgs(obj)


def _active_layer_vg_maps(obj, layer_idx: int):
    """Return ({vg.index: bone_name}, {bone_name: vg.index}, mask_vg_index|None)
    for Layer *layer_idx*'s resident temp-VG set."""
    try:
        id_to_bone = {int(k): v for k, v in json.loads(obj.get("__ssp_meta_map", "{}")).items()}
    except Exception:
        id_to_bone = {}
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


def _read_all_via_bmesh(mesh, idx_to_bone, mask_idx):
    """Whole-mesh variant of the per-vertex ``groups`` read: one bmesh copy and
    C-level ``items()`` per vertex avoid creating an RNA object per group.
    Returns None when the mesh has no deform data or bmesh is unavailable, so
    the caller falls back to the RNA loop."""
    import bmesh

    try:
        bm = bmesh.new()
    except Exception:
        return None
    try:
        bm.from_mesh(mesh)
        deform = bm.verts.layers.deform.active
        if deform is None:
            return {}, {}
        layer_dict = {}
        mask_dict = {}
        for bv in bm.verts:
            entries = None
            for group, weight in bv[deform].items():
                bone = idx_to_bone.get(group)
                if bone is not None:
                    if weight > 0.0:
                        if entries is None:
                            entries = layer_dict.setdefault(bv.index, {})
                        entries[bone] = weight
                elif group == mask_idx:
                    mask_dict[bv.index] = weight
        return layer_dict, mask_dict
    except Exception:
        return None
    finally:
        bm.free()


def read_active_layer_from_mesh(obj, dirty_verts=None) -> tuple:
    """Single-pass read of the active Layer's temp VGs from mesh.vertices,
    restricted to *dirty_verts* when given.
    Returns (layer_dict, mask_dict, layer_idx)."""
    layer_idx = int(obj.get("__ssp_meta_layer", 0))
    idx_to_bone, _, mask_idx = _active_layer_vg_maps(obj, layer_idx)
    layer_dict = {}
    mask_dict = {}
    if dirty_verts is None:
        bulk = _read_all_via_bmesh(obj.data, idx_to_bone, mask_idx)
        if bulk is not None:
            layer_dict, mask_dict = bulk
            _prune_zero_weights(layer_dict)
            return layer_dict, mask_dict, layer_idx
    mesh_vertices = obj.data.vertices
    if dirty_verts is None:
        vertices = mesh_vertices
    else:
        vertices = (mesh_vertices[i] for i in sorted(dirty_verts))
    for v in vertices:
        for g in v.groups:
            bone = idx_to_bone.get(g.group)
            if bone is not None:
                if g.weight > 0.0:
                    layer_dict.setdefault(v.index, {})[bone] = g.weight
            elif g.group == mask_idx:
                mask_dict[v.index] = g.weight
    _prune_zero_weights(layer_dict)
    return layer_dict, mask_dict, layer_idx


def _normalize_for_compare(d: dict) -> dict:
    return {
        int(v): {k: round(float(w), 4) for k, w in ws.items() if float(w) > _WP_EPSILON}
        for v, ws in d.items() if ws
    }


def _normalize_mask_for_compare(d: dict) -> dict:
    return {int(v): round(float(w), 4) for v, w in d.items()}


def _pull_dirty_to_storage(obj, storage, dirty_verts) -> bool:
    layer_dict, mask_dict, layer_idx = read_active_layer_from_mesh(obj, dirty_verts)
    mask_dict = _strip_mask_default(mask_dict, layer_mask_default(storage, layer_idx))
    stored_layer = storage.read_layer_dict(layer_idx)
    stored_mask = storage.read_mask_dict(layer_idx)
    layer_changed = mask_changed = False
    for v in dirty_verts:
        new_w = layer_dict.get(v)
        old_w = stored_layer.get(v)
        if new_w:
            if _normalize_for_compare({v: new_w}) != _normalize_for_compare({v: old_w or {}}):
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
    if layer_changed:
        storage.write_layer_dict(layer_idx, stored_layer)
    if mask_changed:
        if stored_mask:
            storage.write_mask_dict(layer_idx, stored_mask)
        else:
            storage.delete_mask_property(layer_idx)
    return layer_changed or mask_changed


def pull_temp_to_storage(obj, storage, dirty_verts=None) -> bool:
    """Copy the active Layer's temp-VG state into ss_layer_N / ss_mask_N when
    it differs. With *dirty_verts*, only those vertices are read and merged
    into the stored dicts. Returns True if anything was written."""
    if dirty_verts is not None:
        return _pull_dirty_to_storage(obj, storage, dirty_verts)
    layer_dict, mask_dict, layer_idx = read_active_layer_from_mesh(obj)
    mask_default = layer_mask_default(storage, layer_idx)
    mask_dict = _strip_mask_default(mask_dict, mask_default)
    changed = False
    if _normalize_for_compare(layer_dict) != _normalize_for_compare(storage.read_layer_dict(layer_idx)):
        storage.write_layer_dict(layer_idx, layer_dict)
        changed = True
    stored_mask = _strip_mask_default(storage.read_mask_dict(layer_idx), mask_default)
    if _normalize_mask_for_compare(mask_dict) != _normalize_mask_for_compare(stored_mask):
        if mask_dict:
            storage.write_mask_dict(layer_idx, mask_dict)
        else:
            storage.delete_mask_property(layer_idx)
        changed = True
    return changed


_BMESH_PUSH_MIN_VERTS = 256


def _push_via_bmesh(obj, layer_str, mask_dict, verts, idx_to_bone, bone_to_idx, mask_idx,
                    mask_default=0.0) -> None:
    """Bulk variant of the per-vertex VertexGroup.add/remove loop: one bmesh
    round trip regardless of entry count. Below the size threshold the fixed
    from_mesh/to_mesh cost outweighs the per-entry RNA cost, so small brush
    dabs stay on the loop."""
    import array
    import bmesh

    mesh = obj.data
    write_layer = layer_str is not None
    write_mask = mask_dict is not None and mask_idx is not None

    # from_mesh/to_mesh flushes vertex selection from edges/faces, which
    # would widen a vertex-only selection.
    selection = array.array('b', bytes(len(mesh.vertices)))
    mesh.vertices.foreach_get("select", selection)

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        deform = bm.verts.layers.deform.verify()
        bm.verts.ensure_lookup_table()
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
        bm.to_mesh(mesh)
    finally:
        bm.free()

    mesh.vertices.foreach_set("select", selection)


def apply_vg_deltas(vgs, removals: dict, adds: dict) -> None:
    """Apply per-vertex weight deltas with one vertex-group call per group
    (removals) or per (group, weight) pair (adds) instead of one per vertex."""
    for gi, v_list in removals.items():
        vgs[gi].remove(v_list)
    for (gi, w), v_list in adds.items():
        vgs[gi].add(v_list, w, 'REPLACE')


def push_layer_to_temp_vgs(obj, layer_str: dict, mask_dict: dict = None,
                           dirty_verts=None, live_cache: dict = None) -> None:
    """Write string-keyed weights (and, when given, the mask) into the active
    Layer's temp VGs so the paint surface matches storage. Restricted to
    *dirty_verts* when given.

    *live_cache* (per-stroke ``{v_idx: {group: weight}}``) marks a live brush
    write: the bmesh round trip is skipped and current per-vertex state is
    served from, and kept in, that dict instead of being re-read from the mesh."""
    layer_idx = int(obj.get("__ssp_meta_layer", 0))
    idx_to_bone, bone_to_idx, mask_idx = _active_layer_vg_maps(obj, layer_idx)
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


def reload_active_layer_temp_vgs(obj, storage) -> None:
    """Rebuild the active Layer's temp VGs from storage (after a structural
    change such as Merge rewrote ss_layer_N)."""
    layer_idx = int(obj.get("__ssp_meta_layer", 0))
    _, id_to_bone = storage.get_unified_mapping(obj)
    load_layer_to_temp_vgs(obj, storage.read_layer_dict(layer_idx),
                           storage.read_mask_dict(layer_idx), layer_idx, id_to_bone,
                           layer_mask_default(storage, layer_idx))
