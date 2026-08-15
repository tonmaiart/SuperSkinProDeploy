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

PREFIX = "__ssp_"
MASK_VG_NAME = "__ssp_m"
META_VG_NAME = "__ssp_meta"
POOL_VG_NAME = "__ssp_pool"

# Multi-select pool draw-time read cache, keyed by obj.name. Blender's own
# BMesh undo reverts __ssp_pool weights directly (bypassing pool_set_bone_bm()/
# write_pool_names_bm() below), so this cache must be explicitly invalidated
# via bump_pool_epoch() after undo/redo -- see core/ui_controller/undo_manager.py's
# _sync_after_undo(). Without this cache, read_pool_names_from_bm() (called
# from a per-redraw draw handler and every Deform Bones list row) would be an
# O(num_verts) BMesh scan per row per redraw tick.
_pool_epoch: dict = {}   # {obj_name: int}
_pool_cache: dict = {}   # {obj_name: (epoch_at_cache_time, set[str])}


def bump_pool_epoch(obj) -> None:
    """Invalidate read_pool_names_from_bm()'s cache for this object."""
    key = obj.name
    _pool_epoch[key] = _pool_epoch.get(key, 0) + 1


def is_temp_vg(vg_name: str) -> bool:
    """Return True if vg_name is a SuperSkinPro temp VG (should be hidden from UI)."""
    return vg_name.startswith(PREFIX)


def has_temp_vgs(obj) -> bool:
    """Return True if obj currently has temp VGs loaded."""
    return any(vg.name == META_VG_NAME for vg in obj.vertex_groups)


def load_layer_to_temp_vgs(obj, layer_dict: dict, mask_dict: dict,
                            active_layer_index: int, id_to_bone: dict,
                            selected_pool_names: set = None):
    """Write layer data into temp VGs. Call in OBJECT mode.

    Args:
        obj: mesh object
        layer_dict: {v_idx: {bone_name: weight}} — string-keyed (storage format)
        mask_dict: {v_idx: float}
        active_layer_index: int slot index of the layer being loaded
        id_to_bone: {vg_index: bone_name} mapping
        selected_pool_names: optional set of bone names currently in the
            multi-select pool (real + orphan names mixed is fine — names
            with no real vertex group are silently skipped here). Seeds the
            __ssp_pool marker VG so the pool survives Edit-Mode undo the same
            way weight edits do. `None` (default) creates an empty
            __ssp_pool with nothing seeded.
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

    # Pool marker VG -- see POOL_VG_NAME's module docstring note. Created
    # upfront here (never lazily mid-Edit-Mode-session) alongside the other
    # temp VGs above, encoding "bone at real vg_index k is in the pool" as
    # nonzero weight on mesh vertex k. Only real bones (a name resolving to
    # an actual, non-__ssp_-prefixed vertex group whose own index is within
    # the mesh's vertex count) can be encoded this way; orphan names or
    # names beyond vertex count are silently skipped -- callers are expected
    # to track those separately (obj.superskin_storage.selected_orphan_names).
    pool_vg = obj.vertex_groups.new(name=POOL_VG_NAME)
    if selected_pool_names:
        real_name_to_idx = {vg.name: vg.index for vg in obj.vertex_groups
                             if not vg.name.startswith(PREFIX)}
        num_verts = len(obj.data.vertices)
        for name in selected_pool_names:
            vg_idx = real_name_to_idx.get(name)
            if vg_idx is None or vg_idx >= num_verts:
                continue
            pool_vg.add([vg_idx], 1.0, 'REPLACE')


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


def prepare_temp_vg_write(obj, layer_str: dict, id_to_bone: dict,
                           dirty_verts: set = None) -> tuple:
    """Compute the temp-VG (__ssp_*) write data for `layer_str` -- creating
    any new __ssp_N VGs needed for bones that gained weight since load time
    -- WITHOUT touching the BMesh deform layer at all.

    Split out of `write_layer_to_temp_vgs_bm()` so a caller that is about to
    do its own per-vertex BMesh pass anyway (`core/ui_controller/pipeline.py`'s
    `flatten_to_mesh_edit()`, via its `temp_vg_new_weights`/
    `temp_vg_all_ssp_indices` parameters) can fold the actual write into that
    single combined loop instead of `write_layer_to_temp_vgs_bm()` running a
    second, separate `bm.verts` scan over the same vertices just to apply
    the same kind of per-vertex dict update. `write_layer_to_temp_vgs_bm()`
    itself still calls this internally and keeps its own standalone
    behavior unchanged for callers that don't need the merge.

    Args:
        obj: mesh object.
        layer_str: {v_idx: {bone_name: weight}} -- same COMPLETE-active-layer
            contract as write_layer_to_temp_vgs_bm()'s own `layer_str` arg.
        id_to_bone: {vg_index: bone_name} mapping.
        dirty_verts: same restriction semantics as
            write_layer_to_temp_vgs_bm()'s own `dirty_verts` arg -- limits
            which vertices are scanned to decide if a new __ssp_N VG is
            needed, not which vertices exist in the returned `new_weights`.

    Returns:
        (new_weights, all_ssp_indices) --
        new_weights: {v_idx (int): {vg_index (int): weight (float)}}, the
            exact per-vertex temp-VG target state a caller's own BMesh loop
            should write (same shape/semantics as this function's internal
            former local of the same name).
        all_ssp_indices: set[int] of every __ssp_* VG's BMesh deform-layer
            index -- the "clear if present here but absent from new_weights"
            scope a caller's loop needs, mirroring
            write_layer_to_temp_vgs_bm()'s own `all_ssp_indices` local.
    """
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

    # NOTE: mask_vg_idx is intentionally NOT added to all_ssp_indices here.
    # all_ssp_indices is the "clear-if-absent-from-new" scope for the BONE
    # weight sync loop, and `new_weights` (built from layer_str) never
    # contains the mask VG index. Adding mask_vg_idx to this set previously
    # caused the bone-weight loop to delete the mask VG entry for every
    # vertex on every single write, regardless of mask_dict — see
    # docs/bug-history/0020.
    all_ssp_indices: set = set(ssp_vg_idx_map.values())

    new_weights: dict = {}
    for v_idx, bone_weights in layer_str.items():
        entry: dict = {}
        for bone_name, w in bone_weights.items():
            gi = ssp_vg_idx_map.get(bone_name)
            if gi is not None and float(w) > 0.0:
                entry[gi] = float(w)
        new_weights[int(v_idx)] = entry

    return new_weights, all_ssp_indices


def write_layer_to_temp_vgs_bm(obj, mesh, layer_str: dict, id_to_bone: dict,
                                mask_dict: dict = None, dirty_verts: set = None,
                                sync_mesh: bool = True) -> None:
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
        sync_mesh: when False, skips the `bmesh.update_edit_mesh()` call at
            the end of this function. `update_edit_mesh()` syncs BMesh state
            to the Mesh datablock (for rendering / non-BMesh API reads) --
            it has no bearing on whether a LATER BMesh-level read (e.g.
            `read_temp_vgs_from_bm()`, or `flatten_to_mesh_edit()`'s own
            reads via `active_layer_override`) sees the writes made here,
            since those always operate on the same live edit-BMesh object
            regardless of this sync. `True` (default) preserves the original
            behavior for any standalone caller. Both current callers
            (`core/facade/write.py`'s `_write_active_layer_string()` and
            `write_active_layer_from_calc()`) pass `False`, because each
            always calls `core/ui_controller/pipeline.py`'s
            `flatten_to_mesh_edit()` immediately afterward in the same
            synchronous call chain (nothing else runs in between, so nothing
            ever observes the mesh in its not-yet-synced state), and that
            function already ends with its own `bmesh.update_edit_mesh(mesh)`
            call using the same effective flags
            (`loop_triangles=True, destructive=False`, Blender's own
            defaults) -- so the single sync at the end of
            `flatten_to_mesh_edit()` already covers both this function's
            temp-VG writes and its own real-VG writes. Calling it twice back
            to back produced an identical final mesh state to calling it
            once at the end, just at roughly double the native BMesh->Mesh
            sync cost per write tick.
    """
    import bmesh as _bm

    new_weights, all_ssp_indices = prepare_temp_vg_write(obj, layer_str, id_to_bone, dirty_verts)

    mask_vg = obj.vertex_groups.get(MASK_VG_NAME)
    mask_vg_idx = mask_vg.index if mask_vg is not None else None

    bm = _bm.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    deform = bm.verts.layers.deform.verify()

    for bv in ((bm.verts[i] for i in dirty_verts) if dirty_verts is not None else bm.verts):
        v_deform = bv[deform]
        new = new_weights.get(bv.index, {})
        for gi in list(v_deform.keys()):
            if gi in all_ssp_indices and gi not in new:
                del v_deform[gi]
        for gi, w in new.items():
            v_deform[gi] = w

    if mask_dict is not None and mask_vg_idx is not None:
        for bv in ((bm.verts[i] for i in dirty_verts) if dirty_verts is not None else bm.verts):
            v_deform = bv[deform]
            w = mask_dict.get(bv.index)
            if w and float(w) > 0.0:
                v_deform[mask_vg_idx] = float(w)
            elif mask_vg_idx in v_deform:
                del v_deform[mask_vg_idx]

    if sync_mesh:
        _bm.update_edit_mesh(mesh, loop_triangles=True, destructive=False)


def get_active_layer_from_meta(obj) -> int:
    """Read active layer index from object metadata props. Returns -1 if not found."""
    if not has_temp_vgs(obj):
        return -1
    return int(obj.get("__ssp_meta_layer", -1))


# ═══════════════════════════════════════════════════════════════════════
# Multi-select pool (__ssp_pool) — see POOL_VG_NAME's module-level comment.
# ═══════════════════════════════════════════════════════════════════════

def _real_name_to_idx(obj) -> dict:
    """{bone_name: vg_index} for every non-temp vertex group on obj."""
    return {vg.name: vg.index for vg in obj.vertex_groups
            if not vg.name.startswith(PREFIX)}


def read_pool_names(obj) -> set:
    """Object-Mode read of the __ssp_pool marker VG -> set of real bone names
    currently "in the pool". Empty set if __ssp_pool doesn't exist yet."""
    pool_vg = obj.vertex_groups.get(POOL_VG_NAME)
    if pool_vg is None:
        return set()
    idx_to_name = {idx: name for name, idx in _real_name_to_idx(obj).items()}
    result = set()
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == pool_vg.index and g.weight > 0.0:
                name = idx_to_name.get(v.index)
                if name is not None:
                    result.add(name)
    return result


def read_pool_names_from_bm(bm, obj) -> set:
    """Edit-Mode BMesh read of the __ssp_pool marker VG, epoch-cached (see
    bump_pool_epoch()). Called from a per-redraw draw handler
    (features/bone_picker/deform_overlay.py) and every Deform Bones list
    row, so an uncached full BMesh scan on every call would be a real
    per-tick cost -- this mirrors the caching read_temp_vgs_from_bm() does
    not need (it's only called once per weight-op tick, not once per row
    per redraw)."""
    key = obj.name
    epoch = _pool_epoch.get(key, 0)
    cached = _pool_cache.get(key)
    if cached is not None and cached[0] == epoch:
        return cached[1]

    pool_vg = obj.vertex_groups.get(POOL_VG_NAME)
    result = set()
    if pool_vg is not None:
        idx_to_name = {idx: name for name, idx in _real_name_to_idx(obj).items()}
        bm.verts.ensure_lookup_table()
        deform = bm.verts.layers.deform.active
        if deform is not None:
            for bv in bm.verts:
                w = bv[deform].get(pool_vg.index)
                if w and w > 0.0:
                    name = idx_to_name.get(bv.index)
                    if name is not None:
                        result.add(name)

    _pool_cache[key] = (epoch, result)
    return result


def pool_set_bone_bm(obj, mesh, bone_name: str, in_pool: bool) -> bool:
    """O(1) single-bone pool toggle for bone_picker's sweep-add/remove hot
    path (fires on every MOUSEMOVE). Call in EDIT mode.

    Returns False (no-op) if bone_name has no real vertex group, or its own
    vg_index is beyond the mesh's vertex count -- caller should fall back to
    obj.superskin_storage.selected_orphan_names in that case. Returns True
    on success.

    Deliberately does NOT call bmesh.update_edit_mesh() -- bmesh.from_edit_mesh()
    always returns a reference to the single persistent active edit BMesh, so
    the write is immediately visible to a subsequent read_pool_names_from_bm()
    call within the same session without a mesh sync (update_edit_mesh() only
    pushes BMesh state out to the Mesh datablock for depsgraph/render
    consumers, which __ssp_pool -- an internal signaling VG only ever read
    back via BMesh -- has no need for). Skipping it keeps this cheap enough
    to call on every MOUSEMOVE.
    """
    pool_vg = obj.vertex_groups.get(POOL_VG_NAME)
    real_vg = obj.vertex_groups.get(bone_name)
    if pool_vg is None or real_vg is None or real_vg.name.startswith(PREFIX):
        return False

    import bmesh as _bm
    bm = _bm.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    if real_vg.index >= len(bm.verts):
        return False

    deform = bm.verts.layers.deform.verify()
    v_deform = bm.verts[real_vg.index][deform]
    if in_pool:
        v_deform[pool_vg.index] = 1.0
    elif pool_vg.index in v_deform:
        del v_deform[pool_vg.index]

    bump_pool_epoch(obj)
    return True


def write_pool_names_bm(obj, mesh, names: set) -> None:
    """Full-mesh __ssp_pool rewrite for bulk pool writes (Select All, Clear
    All, modal cancel/revert). Call in EDIT mode. `names` may mix real and
    orphan bone names -- names with no real vertex group (or whose vg_index
    is beyond the mesh's vertex count) are silently skipped, same contract
    as pool_set_bone_bm(). Same update_edit_mesh()-skip rationale as
    pool_set_bone_bm() above."""
    pool_vg = obj.vertex_groups.get(POOL_VG_NAME)
    if pool_vg is None:
        return

    import bmesh as _bm
    bm = _bm.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    deform = bm.verts.layers.deform.verify()
    num_verts = len(bm.verts)

    real_name_to_idx = _real_name_to_idx(obj)
    target_indices = {
        real_name_to_idx[name] for name in names
        if name in real_name_to_idx and real_name_to_idx[name] < num_verts
    }

    for bv in bm.verts:
        v_deform = bv[deform]
        if bv.index in target_indices:
            v_deform[pool_vg.index] = 1.0
        elif pool_vg.index in v_deform:
            del v_deform[pool_vg.index]

    bump_pool_epoch(obj)
