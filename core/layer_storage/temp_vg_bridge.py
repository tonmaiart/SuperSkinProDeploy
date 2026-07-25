"""Temporary Vertex Group bridge — active layer ↔ Blender BMesh undo.

While in Edit Mode, the active layer's weights live in __ssp_* VGs so
Blender's native BMesh undo tracks them automatically.

Naming convention (index-based, avoids 64-char bone-name limit):
    __ssp_0      → bone at vg_index 0
    __ssp_1      → bone at vg_index 1
    ...
    __ssp_m      → mask weights
    __ssp_meta   → metadata VG (custom props only, no vertex weights)
                   custom prop "ssp_layer" = int active layer index
                   custom prop "ssp_map"   = JSON str "{vg_index: bone_name}"

All functions operate in OBJECT mode. Callers must ensure mode before calling.
"""

import json

from ...core_subsystems.debug_logging import DebugLogService

PREFIX = "__ssp_"
MASK_VG_NAME = "__ssp_m"
META_VG_NAME = "__ssp_meta"


def is_temp_vg(vg_name: str) -> bool:
    """Return True if vg_name is a SuperSkinPro temp VG (should be hidden from UI)."""
    return vg_name.startswith(PREFIX)


def has_temp_vgs(obj) -> bool:
    """Return True if obj currently has temp VGs loaded."""
    return any(vg.name == META_VG_NAME for vg in obj.vertex_groups)


def load_layer_to_temp_vgs(obj, layer_dict: dict, mask_dict: dict,
                            active_layer_index: int, id_to_bone: dict):
    """Write layer data into temp VGs. Call in OBJECT mode.

    Args:
        obj: mesh object
        layer_dict: {v_idx: {bone_name: weight}} — string-keyed (storage format)
        mask_dict: {v_idx: float}
        active_layer_index: int slot index of the layer being loaded
        id_to_bone: {vg_index: bone_name} mapping
    """
    bone_to_vg_index = {name: idx for idx, name in id_to_bone.items()}

    delete_temp_vgs(obj)

    # Create a temp VG for every bone in the unified mapping, regardless of
    # whether it carries weight in this layer. This guarantees that
    # apply_active_bone() can always locate __ssp_N for any bone, including
    # those whose weights are currently zero.
    vg_index_to_temp_name = {}
    for vg_idx, _bone_name in id_to_bone.items():
        temp_name = f"{PREFIX}{vg_idx}"
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
        temp_name = vg_index_to_temp_name[vg_idx]
        vg = obj.vertex_groups.get(temp_name)
        if vg is None:
            continue
        for v_int, weight in entries:
            vg.add([v_int], weight, 'REPLACE')

    mask_vg = obj.vertex_groups.new(name=MASK_VG_NAME)
    for v_idx, weight in mask_dict.items():
        mask_vg.add([int(v_idx)], float(weight), 'REPLACE')

    obj.vertex_groups.new(name=META_VG_NAME)
    obj["__ssp_meta_layer"] = active_layer_index
    obj["__ssp_meta_map"] = json.dumps({str(k): v for k, v in id_to_bone.items()})


def read_temp_vgs_to_layer(obj) -> tuple:
    """Read temp VGs back into layer_dict and mask_dict. Call in OBJECT mode.

    Returns:
        (layer_dict, mask_dict, active_layer_index)
        layer_dict: {v_idx: {bone_name: weight}}
        mask_dict:  {v_idx: float}
        active_layer_index: int
    """
    meta_vg = obj.vertex_groups.get(META_VG_NAME)
    if meta_vg is None:
        return {}, {}, 0

    active_layer_index = int(obj.get("__ssp_meta_layer", 0))
    map_raw = obj.get("__ssp_meta_map", "{}")
    try:
        id_to_bone = {int(k): v for k, v in json.loads(map_raw).items()}
    except Exception:
        id_to_bone = {}

    mesh = obj.data

    layer_dict = {}
    for vg in obj.vertex_groups:
        if not vg.name.startswith(PREFIX):
            continue
        if vg.name in (MASK_VG_NAME, META_VG_NAME):
            continue
        suffix = vg.name[len(PREFIX):]
        try:
            vg_idx = int(suffix)
        except ValueError:
            continue
        bone_name = id_to_bone.get(vg_idx)
        if bone_name is None:
            continue
        for v in mesh.vertices:
            for g in v.groups:
                if g.group == vg.index and g.weight > 0.0:
                    layer_dict.setdefault(v.index, {})[bone_name] = g.weight

    mask_dict = {}
    mask_vg = obj.vertex_groups.get(MASK_VG_NAME)
    if mask_vg:
        for v in mesh.vertices:
            for g in v.groups:
                if g.group == mask_vg.index and g.weight > 0.0:
                    mask_dict[v.index] = g.weight

    _prune_zero_weights(layer_dict)

    return layer_dict, mask_dict, active_layer_index


def _prune_zero_weights(layer_dict: dict):
    """Remove bone entries with zero weight across all vertices in-place.

    Called after reading temp VGs so that orphan bones scaled to 0 by
    weight ops don't survive into storage as zero-weight noise.
    """
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


def delete_temp_vgs(obj):
    """Remove all __ssp_* VGs and metadata props. Call in OBJECT mode."""
    to_remove = [vg for vg in obj.vertex_groups if vg.name.startswith(PREFIX)]
    for vg in to_remove:
        obj.vertex_groups.remove(vg)
    obj.pop("__ssp_meta_layer", None)
    obj.pop("__ssp_meta_map", None)


def read_temp_vgs_from_bm(bm, obj) -> tuple:
    """Read temp VG weights directly from a BMesh's deform layer.

    Equivalent to read_temp_vgs_to_layer() but operates on the BMesh
    instead of mesh.vertices, so changes made to the BMesh in Edit Mode
    are immediately visible without an update_from_editmode() round-trip.
    Use this whenever the caller already holds the active edit BMesh.

    Always scans every vertex -- callers that feed this into a layer
    compositor need the complete active-layer picture, since a partial scan
    would make untouched vertices look like they have zero weight to
    whatever consumes the result. See core/ui_controller/pipeline.py's
    flatten_to_mesh_edit() docstring for why its `dirty_verts` optimization
    does not extend to this function.

    Returns:
        (layer_dict, mask_dict, active_layer_index)
        layer_dict: {v_idx: {bone_name: weight}}
        mask_dict:  {v_idx: float}
        active_layer_index: int
    """
    meta_vg = obj.vertex_groups.get(META_VG_NAME)
    if meta_vg is None:
        return {}, {}, 0

    active_layer_index = int(obj.get("__ssp_meta_layer", 0))
    map_raw = obj.get("__ssp_meta_map", "{}")
    try:
        id_to_bone = {int(k): v for k, v in json.loads(map_raw).items()}
    except Exception:
        id_to_bone = {}

    # Build map: actual VG list index of __ssp_N → bone_name
    ssp_idx_to_bone: dict = {}
    for vg in obj.vertex_groups:
        if not vg.name.startswith(PREFIX) or vg.name in (MASK_VG_NAME, META_VG_NAME):
            continue
        suffix = vg.name[len(PREFIX):]
        try:
            bone_vg_idx = int(suffix)
        except ValueError:
            continue
        bone_name = id_to_bone.get(bone_vg_idx)
        if bone_name is not None:
            ssp_idx_to_bone[vg.index] = bone_name

    mask_vg = obj.vertex_groups.get(MASK_VG_NAME)
    mask_vg_idx = mask_vg.index if mask_vg is not None else None

    bm.verts.ensure_lookup_table()
    deform = bm.verts.layers.deform.active
    if deform is None:
        return {}, {}, active_layer_index

    layer_dict: dict = {}
    mask_dict: dict = {}

    for bv in bm.verts:
        v_deform = bv[deform]
        for gi, w in v_deform.items():
            if w <= 0.0:
                continue
            if gi in ssp_idx_to_bone:
                layer_dict.setdefault(bv.index, {})[ssp_idx_to_bone[gi]] = w
            elif gi == mask_vg_idx:
                mask_dict[bv.index] = w

    _prune_zero_weights(layer_dict)
    return layer_dict, mask_dict, active_layer_index


def write_layer_to_temp_vgs_bm(obj, mesh, layer_str: dict, id_to_bone: dict,
                                mask_dict: dict = None, dirty_verts: set = None) -> None:
    """Write a string-keyed layer dict back into __ssp_* VGs via BMesh.

    Call in EDIT mode after a weight op. Makes changes visible to
    flatten_to_mesh_edit() without a round-trip through ss_layer_N.
    Creates new __ssp_* VGs for bones that gained weight since load time.
    Does NOT write to ss_layer_N — that bake happens on Exit Edit Mode.

    Args:
        obj: mesh object (must be in EDIT mode)
        mesh: obj.data
        layer_str: {v_idx: {bone_name: weight}} with string bone keys --
            must stay the COMPLETE active layer even when dirty_verts is
            given (only the BMesh *iteration* below is restricted, not this
            lookup dict) -- trimming this to dirty_verts instead would make
            every vertex outside it look "absent" to the sync loops below,
            which treat absence as "clear this vertex's temp VG weight" and
            would silently wipe every untouched vertex's weight-paint color.
        id_to_bone: {vg_index: bone_name} mapping
        mask_dict: optional {v_idx: float} mask updates
        dirty_verts: when given, restricts the two BMesh sync loops below to
            only these vertex indices instead of the whole mesh -- safe
            because any vertex NOT in dirty_verts is guaranteed unchanged
            since the last call (same reasoning already used for
            flatten_to_mesh_edit()'s old_state loop). `None` (default)
            preserves the original full-mesh scan exactly.
    """
    import bmesh as _bm

    if DebugLogService.is_enabled("temp_vg"):
        # Guarded: DebugLogService.log() only gates the print() internally --
        # the f-string argument is still built eagerly by the caller before
        # the call, so an unguarded call here pays this cost on every single
        # gesture tick even when "temp_vg" logging is off (the common case).
        DebugLogService.log(
            "temp_vg",
            f"write_layer_to_temp_vgs_bm() ENTRY: layer_str verts={len(layer_str)} "
            f"mask_dict={'None' if mask_dict is None else f'{len(mask_dict)} verts'}",
        )

    bone_to_vg_index = {name: idx for idx, name in id_to_bone.items()}

    ssp_vg_idx_map: dict = {}
    for vg in obj.vertex_groups:
        if not vg.name.startswith(PREFIX) or vg.name in (MASK_VG_NAME, META_VG_NAME):
            continue
        suffix = vg.name[len(PREFIX):]
        try:
            bone_vg_idx = int(suffix)
        except ValueError:
            continue
        bone_name = id_to_bone.get(bone_vg_idx)
        if bone_name is not None:
            ssp_vg_idx_map[bone_name] = vg.index

    # Restricted to dirty_verts when given: a bone can only gain weight for
    # the first time (needing a fresh __ssp_N VG) on a vertex whose weight
    # actually changed this tick, i.e. one already in dirty_verts. Any bone
    # already carrying weight on a vertex outside dirty_verts must have had
    # its __ssp_N VG created on an earlier tick, when that vertex was itself
    # the one that was dirty -- so skipping non-dirty vertices here can't
    # miss a bone that genuinely needs a new VG.
    all_bone_names: set = set()
    bone_scan_source = (
        (layer_str.get(v, {}) for v in dirty_verts) if dirty_verts is not None
        else layer_str.values()
    )
    for v_weights in bone_scan_source:
        all_bone_names.update(v_weights.keys())
    for bone_name in all_bone_names:
        if bone_name not in ssp_vg_idx_map:
            bone_vg_idx = bone_to_vg_index.get(bone_name)
            if bone_vg_idx is None:
                continue
            temp_name = f"{PREFIX}{bone_vg_idx}"
            new_vg = obj.vertex_groups.new(name=temp_name)
            ssp_vg_idx_map[bone_name] = new_vg.index

    mask_vg = obj.vertex_groups.get(MASK_VG_NAME)
    mask_vg_idx = mask_vg.index if mask_vg is not None else None

    # NOTE: mask_vg_idx is intentionally NOT added to all_ssp_indices here.
    # all_ssp_indices is the "clear-if-absent-from-new" scope for the BONE
    # weight sync loop below, and `new` (built from layer_str) never contains
    # the mask VG index. Adding mask_vg_idx to this set previously caused the
    # bone-weight loop to delete the mask VG entry for every vertex on every
    # single write_layer_to_temp_vgs_bm() call, regardless of mask_dict — see
    # docs/bug-history/0020.
    all_ssp_indices: set = set(ssp_vg_idx_map.values())

    if DebugLogService.is_enabled("temp_vg"):
        # Guarded: sorted(all_ssp_indices) was previously computed on every
        # single call regardless of whether "temp_vg" logging was on.
        DebugLogService.log(
            "temp_vg",
            f"mask_vg_idx={mask_vg_idx} all_ssp_indices={sorted(all_ssp_indices)} "
            f"(mask_vg_idx must NOT appear in all_ssp_indices — see bug-history/0020)",
        )

    bm = _bm.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    deform = bm.verts.layers.deform.verify()

    new_weights: dict = {}
    for v_idx, bone_weights in layer_str.items():
        entry: dict = {}
        for bone_name, w in bone_weights.items():
            gi = ssp_vg_idx_map.get(bone_name)
            if gi is not None and float(w) > 0.0:
                entry[gi] = float(w)
        new_weights[int(v_idx)] = entry

    for bv in ((bm.verts[i] for i in dirty_verts) if dirty_verts is not None else bm.verts):
        v_deform = bv[deform]
        new = new_weights.get(bv.index, {})
        for gi in list(v_deform.keys()):
            if gi in all_ssp_indices and gi not in new:
                del v_deform[gi]
        for gi, w in new.items():
            v_deform[gi] = w

    if DebugLogService.is_enabled("temp_vg"):
        # Direct verification of the clear-if-absent loop just above: scan
        # the BMesh AGAIN right after it ran and count, per __ssp_ bone gi,
        # how many vertices (within this call's dirty_verts scope) still
        # carry nonzero weight for it. Any gi that new_weights never asked
        # for (i.e. every vertex's entry for that bone-name was empty in
        # layer_str) but still shows a nonzero count here means the delete
        # above did not actually take effect for that gi -- as opposed to
        # the bone genuinely still having weight because layer_str said so.
        gi_to_bone = {idx: name for name, idx in ssp_vg_idx_map.items()}
        expected_counts = {}
        for v_idx, entry in new_weights.items():
            for gi in entry:
                expected_counts[gi] = expected_counts.get(gi, 0) + 1
        residual_counts = {}
        for bv2 in ((bm.verts[i] for i in dirty_verts) if dirty_verts is not None else bm.verts):
            v_deform2 = bv2[deform]
            for gi2 in v_deform2.keys():
                if gi2 in all_ssp_indices and v_deform2[gi2] > 0.0:
                    residual_counts[gi2] = residual_counts.get(gi2, 0) + 1
        mismatches = {
            gi_to_bone.get(gi, gi): (residual_counts.get(gi, 0), expected_counts.get(gi, 0))
            for gi in all_ssp_indices
            if residual_counts.get(gi, 0) != expected_counts.get(gi, 0)
        }
        DebugLogService.log(
            "temp_vg",
            f"write_layer_to_temp_vgs_bm() POST-DELETE VERIFICATION: "
            f"per-bone (actual_verts_still_weighted, expected_from_new_weights) "
            f"mismatches={mismatches!r} (empty means every __ssp_ bone's live "
            f"BMesh vertex count exactly matches what new_weights asked for -- "
            f"a non-empty entry means the delete loop above failed to clear "
            f"that bone for some vertices within this call's dirty_verts scope)",
        )

    if mask_dict is not None and mask_vg_idx is not None:
        for bv in ((bm.verts[i] for i in dirty_verts) if dirty_verts is not None else bm.verts):
            v_deform = bv[deform]
            w = mask_dict.get(bv.index)
            if w and float(w) > 0.0:
                v_deform[mask_vg_idx] = float(w)
            elif mask_vg_idx in v_deform:
                del v_deform[mask_vg_idx]

    DebugLogService.log("temp_vg", "write_layer_to_temp_vgs_bm() EXIT: BMesh updated")

    _bm.update_edit_mesh(mesh, loop_triangles=True, destructive=False)


def get_active_layer_from_meta(obj) -> int:
    """Read active layer index from object metadata props. Returns -1 if not found."""
    if not has_temp_vgs(obj):
        return -1
    return int(obj.get("__ssp_meta_layer", -1))
