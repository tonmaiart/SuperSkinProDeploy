"""Temporary Vertex Group bridge — per-Layer cache ↔ Blender BMesh undo.

While in Edit Mode, each Layer visited this session gets its OWN set of
__ssp_* VGs, so Blender's native BMesh undo tracks them automatically and
multiple Layers' in-progress edits can coexist without any one of them
being baked back to permanent storage (ss_layer_N/ss_mask_N) until the
session actually exits Edit Mode.

Naming convention (per-layer namespaced, index-based to avoid the 64-char
bone-name limit):
    __ssp_L{layer_idx}_{vg_idx}  → bone at vg_index, for Layer layer_idx
    __ssp_L{layer_idx}_m         → mask weights, for Layer layer_idx
    __ssp_meta   → session marker VG (no vertex weights of its own).
                   Payload lives in object custom props, shared across
                   every resident Layer this session:
                     "__ssp_meta_layer" = int, the CURRENTLY ACTIVE layer index
                     "__ssp_meta_map"   = JSON str "{vg_index: bone_name}"
                                          (a property of the mesh's armature,
                                          not of any one Layer)
    __ssp_pool   → multi-select UI pool. Global/session-wide, NOT per-layer
                   -- which bones are ctrl/shift-selected in the Deform
                   Bones list is independent of which Layer is active.

All functions operate in OBJECT mode unless noted (the *_bm variants
operate directly on a live Edit-Mode BMesh). Callers must ensure mode
before calling.
"""

import contextlib
import json

import bpy

# Suppresses write_pending_meta_list()'s own undo_push() call -- used by
# core/ui_controller/layer_crud.py's compound layer-CRUD ops (create/
# remove/duplicate/merge Layer), which need exactly ONE checkpoint for the
# whole action (pushed by the caller itself once everything settles) rather
# than an early one here PLUS the caller's own final one. A plain module
# flag is fine -- Blender's Python API is single-threaded on the main
# thread, no concurrency to guard against.
#
# NOTE: bpy.context.preferences.edit.use_global_undo was tried first as the
# "real" Blender-native way to batch-suppress undo pushes and measured to
# have NO effect on this scenario's nested bpy.ops.object.mode_set() /
# bpy.ops.ed.undo_push() calls -- it does not gate whatever mechanism those
# use. This flag is a from-scratch replacement under our own control.
_suppress_meta_undo_push = False


@contextlib.contextmanager
def suppress_meta_undo_push():
    global _suppress_meta_undo_push
    prev = _suppress_meta_undo_push
    _suppress_meta_undo_push = True
    try:
        yield
    finally:
        _suppress_meta_undo_push = prev


PREFIX = "__ssp_"
_LAYER_PREFIX = "__ssp_L"
META_VG_NAME = "__ssp_meta"
POOL_VG_NAME = "__ssp_pool"

# __ssp_meta_list storage -- chunked BMesh string customdata layers, all
# read/written at vertex 0 only (never spread across vertex INDEX, which
# would tie capacity to the mesh's own vertex count -- see the mesh-with-1-
# vertex discussion this design followed from). Confirmed empirically:
# changing an EXISTING layer's value survives Edit-Mode undo atomically
# across the whole chunk set; creating/removing the layers themselves does
# not (same class as VG add/remove), so they must be pre-allocated once in
# OBJECT mode (_ensure_meta_chunk_layers(), called from
# load_layer_to_temp_vgs()) and never lazily created mid-Edit-Mode-session.
#
# KNOWN COST (accepted deliberately, not an oversight): unlike VG deform
# weights, a generic customdata layer is DENSE -- each chunk allocates
# _META_CHUNK_SIZE bytes per VERTEX IN THE WHOLE MESH, not just vertex 0.
# At _META_NUM_CHUNKS=40 that's ~10KB of usable metadata storage but
# ~100MB of extra RAM per 100k mesh vertices, PERMANENTLY for the mesh's
# lifetime (not just during an Edit Mode session -- see below). A
# companion-object design (a separate 1-vertex object holding these
# layers, decoupling cost from the edited mesh's vertex count) was
# considered but not validated -- its own undo behavior while a DIFFERENT
# object is the one in Edit Mode is unconfirmed. Revisit if this cost
# becomes a real problem in practice.
#
# PERMANENT, NOT per-session: these layers are created once (the first
# time this mesh ever enters a SuperSkinPro Edit Mode session) and never
# removed. An earlier version created/removed them on every single
# Enter/Exit Edit Mode via a full bmesh.from_mesh()/to_mesh() round trip --
# measured to cause a real Enter/Exit slowdown on production-sized
# character meshes, since that round trip copies the ENTIRE mesh twice
# regardless of how small the actual change is. Only the stored VALUE
# (the length) is reset per session (_reset_meta_len()), via the cheap
# Mesh.attributes value API instead of another bmesh round trip -- see
# _ensure_meta_chunk_layers()'s and _reset_meta_len()'s docstrings.
#
# Sized from real-world measurement, not a guess: a single create_layer()
# call on a real rig mid-session already needed 3017 bytes (10 chunks /
# 2550 bytes was not enough and failed loudly, as designed) -- 40 chunks
# leaves real headroom for larger rigs / more resident Layers before
# hitting write_pending_meta_list()'s RuntimeError again.
_META_CHUNK_PREFIX = "__ssp_meta_list_chunk_"
_META_LEN_LAYER = "__ssp_meta_list_len"
_META_CHUNK_SIZE = 255
_META_NUM_CHUNKS = 40  # budget = 10200 bytes; raise further if write_pending_meta_list() errors again

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
    """Return True if obj currently has a live Edit-Mode session at all
    (i.e. at least one Layer has been loaded into temp VGs this session)."""
    return any(vg.name == META_VG_NAME for vg in obj.vertex_groups)


def has_layer_cache(obj, layer_idx: int) -> bool:
    """Return True if this specific Layer has its own resident temp-VG set
    this session (i.e. it has been visited/loaded already)."""
    return obj.vertex_groups.get(mask_vg_name(layer_idx)) is not None


def weight_vg_name(layer_idx: int, vg_idx: int) -> str:
    """Name of the per-Layer, per-bone temp VG for Layer *layer_idx*'s bone
    at unified vg_index *vg_idx*."""
    return f"{_LAYER_PREFIX}{layer_idx}_{vg_idx}"


def mask_vg_name(layer_idx: int) -> str:
    """Name of the per-Layer mask temp VG for Layer *layer_idx*. Also used
    as the "is this Layer resident this session" existence check (see
    has_layer_cache()) -- always created for a Layer's temp-VG set even
    when its mask is empty."""
    return f"{_LAYER_PREFIX}{layer_idx}_m"


def parse_layer_weight_vg_name(vg_name: str):
    """Parse '__ssp_L{layer_idx}_{vg_idx}' -> (layer_idx, vg_idx).

    Returns None if vg_name isn't a per-Layer WEIGHT VG (a mask VG's suffix
    is the non-numeric "m", so it falls through the int() conversion below
    and returns None here too -- callers that need to distinguish mask VGs
    should check mask_vg_name(layer_idx) explicitly instead)."""
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


def _ensure_meta_chunk_layers(obj) -> None:
    """Pre-allocate the __ssp_meta_list chunk layers (see the module-level
    comment above _META_CHUNK_PREFIX). Call in OBJECT mode, once per
    session, alongside __ssp_meta's own creation.

    PERMANENT once created -- never removed at session end (see the KNOWN
    COST note: removing and recreating them every Enter/Exit Edit Mode via
    a full bmesh.from_mesh()/to_mesh() round trip on a real character mesh
    was the actual cause of a measured Enter/Exit slowdown; the layers
    themselves are cheap to leave allocated, the round trip is what's
    expensive). Only their VALUE needs resetting per session (see
    _reset_meta_len()), not the layers' existence.

    The `all(name in obj.data.attributes ...)` check is a cheap shortcut
    (no mesh copy) to skip the round trip entirely once these layers exist
    from a prior session. If that check is ever wrong for this Blender
    version (Mesh.attributes not surfacing a bmesh-native string/int
    layer), the bm.verts.layers.*.get() checks below are the real source
    of truth and safely no-op for anything that already exists either
    way -- correctness never depends on the shortcut being right, only
    performance does."""
    needed_names = [_META_LEN_LAYER] + [f"{_META_CHUNK_PREFIX}{i}" for i in range(_META_NUM_CHUNKS)]
    if all(name in obj.data.attributes for name in needed_names):
        _reset_meta_len(obj)
        return

    import bmesh as _bm

    bm = _bm.new()
    bm.from_mesh(obj.data)
    if bm.verts.layers.int.get(_META_LEN_LAYER) is None:
        bm.verts.layers.int.new(_META_LEN_LAYER)
    for i in range(_META_NUM_CHUNKS):
        name = f"{_META_CHUNK_PREFIX}{i}"
        if bm.verts.layers.string.get(name) is None:
            bm.verts.layers.string.new(name)

    len_layer = bm.verts.layers.int.get(_META_LEN_LAYER)
    bm.verts.ensure_lookup_table()
    if len(bm.verts) > 0:
        bm.verts[0][len_layer] = 0

    bm.to_mesh(obj.data)
    bm.free()


def _reset_meta_len(obj) -> None:
    """Cheap, no-bmesh reset of the stored length back to 0 at the start of
    a fresh session, so read_pending_meta_list() doesn't mistake a PRIOR
    session's leftover chunk bytes for pending data in THIS session (the
    chunk layers are permanent now -- only their value needs resetting).

    Falls back to the reliable-but-expensive bmesh path if the cheap
    Mesh.attributes value API doesn't behave as expected on this Blender
    version -- correctness of the reset matters more than the optimization,
    unlike the existence check in _ensure_meta_chunk_layers()."""
    try:
        obj.data.attributes[_META_LEN_LAYER].data[0].value = 0
    except Exception:
        import bmesh as _bm
        bm = _bm.new()
        bm.from_mesh(obj.data)
        len_layer = bm.verts.layers.int.get(_META_LEN_LAYER)
        if len_layer is not None:
            bm.verts.ensure_lookup_table()
            if len(bm.verts) > 0:
                bm.verts[0][len_layer] = 0
                bm.to_mesh(obj.data)
        bm.free()


def load_layer_to_temp_vgs(obj, layer_dict: dict, mask_dict: dict,
                            layer_idx: int, id_to_bone: dict,
                            selected_pool_names: set = None):
    """Create Layer *layer_idx*'s own temp-VG set from layer_dict/mask_dict,
    and mark it the active Layer. Call in OBJECT mode.

    Unlike the old single-active-layer design, this does NOT wipe any other
    Layer's already-resident VG set -- multiple Layers' temp-VG sets can
    coexist simultaneously this session (each its own __ssp_L{idx}_*
    namespace), so switching back to an earlier Layer never needs a reload
    from permanent storage.

    Args:
        obj: mesh object
        layer_dict: {v_idx: {bone_name: weight}} — string-keyed (storage format)
        mask_dict: {v_idx: float}
        layer_idx: int slot index of the Layer being loaded
        id_to_bone: {vg_index: bone_name} mapping -- shared across every
            Layer (a property of the mesh's armature, not of any one Layer)
        selected_pool_names: optional set of bone names currently in the
            multi-select pool (real + orphan names mixed is fine — names
            with no real vertex group are silently skipped here). Only
            consulted the FIRST time __ssp_pool is created this session --
            it's a global/session-wide marker (see POOL_VG_NAME above), so
            a later call for a different Layer must not reseed/reset it.
    """
    bone_to_vg_index = {name: idx for idx, name in id_to_bone.items()}

    # Only this Layer's own prior set, if any -- never a blanket wipe of
    # every resident Layer (see delete_temp_vgs()'s layer_idx parameter).
    delete_temp_vgs(obj, layer_idx=layer_idx)

    # Create a temp VG for every bone in the unified mapping, regardless of
    # whether it carries weight in this layer. This guarantees that
    # apply_active_bone() can always locate this Layer's VG for any bone,
    # including those whose weights are currently zero.
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
        temp_name = vg_index_to_temp_name[vg_idx]
        vg = obj.vertex_groups.get(temp_name)
        if vg is None:
            continue
        for v_int, weight in entries:
            vg.add([v_int], weight, 'REPLACE')

    mask_vg = obj.vertex_groups.new(name=mask_vg_name(layer_idx))
    for v_idx, weight in mask_dict.items():
        mask_vg.add([int(v_idx)], float(weight), 'REPLACE')

    if obj.vertex_groups.get(META_VG_NAME) is None:
        obj.vertex_groups.new(name=META_VG_NAME)
        # Only on the genuine first layer-load of the session -- this does
        # a full bmesh.from_mesh()/to_mesh() round trip (expensive on a
        # real character mesh), so it must never repeat on every
        # load_layer_to_temp_vgs() call the way it did before this fix.
        _ensure_meta_chunk_layers(obj)
    obj["__ssp_meta_layer"] = layer_idx
    obj["__ssp_meta_map"] = json.dumps({str(k): v for k, v in id_to_bone.items()})

    # Pool marker VG -- see POOL_VG_NAME's module docstring note. Global
    # across every Layer, so only created (and seeded from
    # selected_pool_names) the FIRST time any Layer is loaded this session
    # -- a later call for a different Layer must leave an already-existing
    # __ssp_pool untouched, or switching to a not-yet-visited Layer would
    # silently wipe the user's current multi-selection.
    if obj.vertex_groups.get(POOL_VG_NAME) is None:
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


def read_temp_vgs_to_layer(obj, layer_idx: int = None) -> tuple:
    """Read one Layer's temp VGs back into layer_dict and mask_dict. Call in
    OBJECT mode.

    Args:
        layer_idx: which Layer's own __ssp_L{layer_idx}_* set to read.
            `None` (default) resolves to the currently ACTIVE Layer
            (obj["__ssp_meta_layer"]) -- preserves the original
            single-active-layer call signature for every caller that only
            ever wants the active Layer's data.

    Returns:
        (layer_dict, mask_dict, active_layer_index)
        layer_dict: {v_idx: {bone_name: weight}}
        mask_dict:  {v_idx: float}
        active_layer_index: int -- the session's currently active Layer
            (not necessarily *layer_idx* when an explicit, non-active
            layer_idx is passed)
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


def delete_temp_vgs(obj, layer_idx: int = None):
    """Remove temp VGs. Call in OBJECT mode.

    layer_idx=None (default): wipe EVERYTHING -- every resident Layer's own
    weight/mask VG set, plus the global __ssp_meta/__ssp_pool markers and
    every __ssp_meta_* custom prop. Used once, at real Exit-time flush.

    layer_idx=<int>: wipe only that one Layer's own weight/mask VGs
    (__ssp_L{layer_idx}_*). Leaves every other resident Layer's VGs, and
    the global __ssp_meta/__ssp_pool markers, untouched. Used by
    load_layer_to_temp_vgs() (clearing a stale prior set for the same
    Layer, if any, before creating a fresh one) and by Remove/Merge
    mid-session.
    """
    if layer_idx is None:
        to_remove = [vg for vg in obj.vertex_groups if vg.name.startswith(PREFIX)]
        for vg in to_remove:
            obj.vertex_groups.remove(vg)
        # __ssp_meta_list_chunk_*/_len customdata layers are deliberately
        # NOT removed here -- see _ensure_meta_chunk_layers()'s docstring
        # (PERMANENT once created, only their value is session-scoped).
        obj.pop("__ssp_meta_layer", None)
        obj.pop("__ssp_meta_map", None)
    else:
        prefix = f"{_LAYER_PREFIX}{layer_idx}_"
        to_remove = [vg for vg in obj.vertex_groups if vg.name.startswith(prefix)]
        for vg in to_remove:
            obj.vertex_groups.remove(vg)


def read_temp_vgs_from_bm(bm, obj, layer_idx: int = None) -> tuple:
    """Read one Layer's temp VG weights directly from a BMesh's deform layer.

    Equivalent to read_temp_vgs_to_layer() but operates on the BMesh
    instead of mesh.vertices, so changes made to the BMesh in Edit Mode
    are immediately visible without an update_from_editmode() round-trip.
    Use this whenever the caller already holds the active edit BMesh.

    Always scans every vertex -- callers that feed this into a layer
    compositor need the complete picture for this one Layer, since a
    partial scan would make untouched vertices look like they have zero
    weight to whatever consumes the result. See core/ui_controller/pipeline.py's
    flatten_to_mesh_edit() docstring for why its `dirty_verts` optimization
    does not extend to this function.

    Args:
        layer_idx: which Layer's own __ssp_L{layer_idx}_* set to read.
            `None` (default) resolves to the currently ACTIVE Layer
            (obj["__ssp_meta_layer"]) -- preserves the original
            single-active-layer call signature for every hot-path caller
            that only ever paints the active Layer.

    Returns:
        (layer_dict, mask_dict, active_layer_index)
        layer_dict: {v_idx: {bone_name: weight}}
        mask_dict:  {v_idx: float}
        active_layer_index: int -- the session's currently active Layer
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

    # Build map: actual VG list index of this Layer's __ssp_L{layer_idx}_N → bone_name
    ssp_idx_to_bone: dict = {}
    for vg in obj.vertex_groups:
        parsed = parse_layer_weight_vg_name(vg.name)
        if parsed is None or parsed[0] != layer_idx:
            continue
        bone_name = id_to_bone.get(parsed[1])
        if bone_name is not None:
            ssp_idx_to_bone[vg.index] = bone_name

    mask_vg = obj.vertex_groups.get(mask_vg_name(layer_idx))
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
                           dirty_verts: set = None, layer_idx: int = None) -> tuple:
    """Compute the temp-VG (__ssp_*) write data for `layer_str` -- creating
    any new __ssp_L{layer_idx}_N VGs needed for bones that gained weight
    since load time -- WITHOUT touching the BMesh deform layer at all.

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
            which vertices are scanned to decide if a new __ssp_L{layer_idx}_N
            VG is needed, not which vertices exist in the returned
            `new_weights`.
        layer_idx: which Layer this write targets. `None` (default)
            resolves to the currently ACTIVE Layer -- every existing caller
            only ever writes the layer currently being painted.

    Returns:
        (new_weights, all_ssp_indices) --
        new_weights: {v_idx (int): {vg_index (int): weight (float)}}, the
            exact per-vertex temp-VG target state a caller's own BMesh loop
            should write (same shape/semantics as this function's internal
            former local of the same name).
        all_ssp_indices: set[int] of every one of THIS Layer's __ssp_* VG
            BMesh deform-layer indices -- the "clear if present here but
            absent from new_weights" scope a caller's loop needs, mirroring
            write_layer_to_temp_vgs_bm()'s own `all_ssp_indices` local.
    """
    if layer_idx is None:
        layer_idx = int(obj.get("__ssp_meta_layer", 0))

    bone_to_vg_index = {name: idx for idx, name in id_to_bone.items()}

    ssp_vg_idx_map: dict = {}
    for vg in obj.vertex_groups:
        parsed = parse_layer_weight_vg_name(vg.name)
        if parsed is None or parsed[0] != layer_idx:
            continue
        bone_name = id_to_bone.get(parsed[1])
        if bone_name is not None:
            ssp_vg_idx_map[bone_name] = vg.index

    # Restricted to dirty_verts when given: a bone can only gain weight for
    # the first time (needing a fresh __ssp_L{layer_idx}_N VG) on a vertex
    # whose weight actually changed this tick, i.e. one already in
    # dirty_verts. Any bone already carrying weight on a vertex outside
    # dirty_verts must have had its VG created on an earlier tick, when that
    # vertex was itself the one that was dirty -- so skipping non-dirty
    # vertices here can't miss a bone that genuinely needs a new VG.
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
            temp_name = weight_vg_name(layer_idx, bone_vg_idx)
            new_vg = obj.vertex_groups.new(name=temp_name)
            ssp_vg_idx_map[bone_name] = new_vg.index

    # NOTE: this Layer's mask VG index is intentionally NOT added to
    # all_ssp_indices here. all_ssp_indices is the "clear-if-absent-from-new"
    # scope for the BONE weight sync loop, and `new_weights` (built from
    # layer_str) never contains the mask VG index. Adding the mask VG index
    # to this set previously caused the bone-weight loop to delete the mask
    # VG entry for every vertex on every single write, regardless of
    # mask_dict — see docs/bug-history/0020.
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
                                sync_mesh: bool = True, layer_idx: int = None) -> None:
    """Write a string-keyed layer dict back into one Layer's __ssp_* VGs via
    BMesh.

    Call in EDIT mode after a weight op. Makes changes visible to
    flatten_to_mesh_edit() without a round-trip through ss_layer_N.
    Creates new __ssp_L{layer_idx}_N VGs for bones that gained weight since
    load time. Does NOT write to ss_layer_N — that bake happens on Exit
    Edit Mode.

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
        layer_idx: which Layer this write targets. `None` (default)
            resolves to the currently ACTIVE Layer -- every existing caller
            only ever writes the layer currently being painted.
    """
    import bmesh as _bm

    if layer_idx is None:
        layer_idx = int(obj.get("__ssp_meta_layer", 0))

    new_weights, all_ssp_indices = prepare_temp_vg_write(
        obj, layer_str, id_to_bone, dirty_verts, layer_idx=layer_idx,
    )

    mask_vg = obj.vertex_groups.get(mask_vg_name(layer_idx))
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
# In-session pending metadata mirror (__ssp_meta_list) — structural layer
# CRUD (Add/Remove/Duplicate/Merge/Move/Rename/Visibility) during Edit Mode
# writes HERE instead of the permanent ss_layers_meta, so it stays fully
# reversible/undo-safe until a real Exit Edit Mode flush
# (core/ui_controller/layer_crud.py's flush_edit_session()) copies the
# final result into ss_layers_meta.
#
# Stored via the chunked BMesh string-layer mechanism (_META_CHUNK_PREFIX /
# _META_LEN_LAYER above), NOT a plain custom property -- a bare
# obj["__ssp_meta_list"] = json.dumps(meta) was the original design and was
# proven, empirically, to NOT be undo-safe: Edit-Mode's lightweight BMesh
# undo does not track custom ID Properties at all (see
# .claude/skills/superskinpro-undo.md's Core Invariant). Changing an
# EXISTING BMesh customdata layer's VALUE is the one thing that undo
# system actually diffs, which is what read/write here rely on.
# ═══════════════════════════════════════════════════════════════════════

def read_pending_meta_list(obj):
    """Read the in-session pending mirror of ss_layers_meta
    (chunked __ssp_meta_list_chunk_* layers). Returns None when no session
    is active, or when nothing has been written yet this session -- caller
    should fall back to permanent storage in that case (see
    effective_meta_list()).

    Call in EDIT mode -- every existing caller already gates on
    obj.mode == 'EDIT' before reaching here (see effective_meta_list())."""
    if not has_temp_vgs(obj):
        return None

    import bmesh as _bm
    bm = _bm.from_edit_mesh(obj.data)
    len_layer = bm.verts.layers.int.get(_META_LEN_LAYER)
    if len_layer is None:
        return None
    bm.verts.ensure_lookup_table()
    if len(bm.verts) == 0:
        return None

    v0 = bm.verts[0]
    total_len = v0[len_layer]
    if not total_len:
        return None

    needed = -(-total_len // _META_CHUNK_SIZE)  # ceil div
    parts = []
    for i in range(needed):
        layer = bm.verts.layers.string.get(f"{_META_CHUNK_PREFIX}{i}")
        parts.append(v0[layer] if layer is not None else b"")
    raw = b"".join(parts)[:total_len]

    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def write_pending_meta_list(obj, meta: list) -> None:
    """Write the in-session pending mirror of ss_layers_meta
    (chunked __ssp_meta_list_chunk_* layers), then push an explicit undo
    checkpoint -- a plain BMesh customdata value change does not reliably
    get its own distinguishable undo step otherwise (same class of gap as
    docs/bug-history/0032). No-op if no session is active -- callers should
    already be gating on this via write_effective_meta_list().

    Call in EDIT mode -- every existing caller already gates on
    obj.mode == 'EDIT' before reaching here (see write_effective_meta_list()).

    Raises RuntimeError if `meta`'s JSON encoding exceeds the pre-allocated
    _META_NUM_CHUNKS budget -- loud and immediate is safer than silently
    truncating (BMesh string layers truncate silently past their 255-byte
    slot, which would corrupt the JSON with no error at all)."""
    if not has_temp_vgs(obj):
        return

    import bmesh as _bm
    bm = _bm.from_edit_mesh(obj.data)
    len_layer = bm.verts.layers.int.get(_META_LEN_LAYER)
    if len_layer is None:
        # _ensure_meta_chunk_layers() should have run in load_layer_to_temp_vgs()
        # already -- nothing to write into if it somehow didn't.
        return

    blob = json.dumps(meta).encode("utf-8")
    needed = -(-len(blob) // _META_CHUNK_SIZE) if blob else 0  # ceil div
    if needed > _META_NUM_CHUNKS:
        raise RuntimeError(
            f"Layer metadata ({len(blob)} bytes) exceeds the "
            f"{_META_NUM_CHUNKS * _META_CHUNK_SIZE}-byte undo-safe storage "
            f"budget -- increase _META_NUM_CHUNKS in temp_vg_bridge.py."
        )

    bm.verts.ensure_lookup_table()
    v0 = bm.verts[0]
    for i in range(needed):
        layer = bm.verts.layers.string.get(f"{_META_CHUNK_PREFIX}{i}")
        v0[layer] = blob[i * _META_CHUNK_SIZE:(i + 1) * _META_CHUNK_SIZE]
    v0[len_layer] = len(blob)

    _bm.update_edit_mesh(obj.data)
    if not _suppress_meta_undo_push:
        bpy.ops.ed.undo_push(message="Change Layer Metadata")


def effective_meta_list(obj, storage) -> list:
    """Read layer metadata from the correct source for the CURRENT mode:
    the in-session pending mirror (__ssp_meta_list) while Edit Mode has an
    active session with one already written, else permanent ss_layers_meta
    storage (``storage.read_meta_list()``). Every structural layer CRUD op
    (Add/Remove/Duplicate/Merge/Move/Rename/Visibility, plus per-layer
    selection/lock/active-bone bookkeeping) reads through this instead of
    ``storage.read_meta_list()`` directly, so changes made mid-session stay
    in the pending mirror until a real Exit Edit Mode flush -- see
    core/ui_controller/layer_crud.py's flush_edit_session()."""
    if obj.mode == 'EDIT':
        pending = read_pending_meta_list(obj)
        if pending is not None:
            return pending
    return storage.read_meta_list()


def write_effective_meta_list(obj, storage, meta: list) -> None:
    """Write layer metadata to the correct target for the CURRENT mode --
    see effective_meta_list(). While an Edit Mode session is active, this
    writes ONLY the pending mirror, never touching permanent storage --
    that only happens once, at flush_edit_session()."""
    if obj.mode == 'EDIT' and has_temp_vgs(obj):
        write_pending_meta_list(obj, meta)
        return
    storage.write_meta_list(meta)


def _mask_values_close(value: float, target: float) -> bool:
    return abs(value - target) < 1e-4


def get_layer_mask_state(obj, storage, layer_index: int) -> str:
    """Classify *layer_index*'s raw per-vertex mask coverage across the
    whole mesh into one of three states: ``'WHITE'`` (every vertex
    effectively fully masked-in, i.e. 1.0), ``'BLACK'`` (every vertex fully
    masked-out, i.e. 0.0), or ``'EDITED'`` (anything else -- a genuine,
    non-uniform per-vertex mask). This is the layer's OWN raw mask, not the
    "cut" result after compositing against sibling layers (see
    ``core_subsystems/layer_compositor/layer_compositor.py``'s
    ``get_layer_cut_mask()`` for that instead).

    Mode-aware: reads this Layer's own resident temp-VG mask cache
    (``__ssp_L{layer_index}_m``) while a live Edit Mode session already has
    one, else permanent ``ss_mask_{layer_index}`` storage. Read-only and
    independent of which Layer is currently active -- never switches the
    active Layer or mutates any state -- so it's safe to call once per
    Layer from a UIList ``draw_item()`` on every single redraw. Backs the
    Layer list's per-row mask-state icon (see
    ``interface/utils/icons.py``'s ``get_layer_state_icon_id()`` and
    ``features/deform_layer_viewer/layer_viewer/ui.py``'s
    ``draw_main_icon_value()``), exposed to feature domains via
    ``CoreFacade.get_layer_mask_state()``.
    """
    meta = effective_meta_list(obj, storage)
    layer_meta = next((layer for layer in meta if layer.get("index") == layer_index), None)
    if layer_meta is None:
        return 'EDITED'
    mask_default = float(layer_meta.get("mask_default", 1.0))

    if obj.mode == 'EDIT' and has_layer_cache(obj, layer_index):
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        _, mask_dict, _ = read_temp_vgs_from_bm(bm, obj, layer_idx=layer_index)
    else:
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


# ═══════════════════════════════════════════════════════════════════════
# Full pool SSOT (real __ssp_pool + orphan selected_orphan_names union/split)
# ═══════════════════════════════════════════════════════════════════════
# Orphan bones have no real vertex group, so they can't use the
# vertex-index-as-slot encoding above -- see SuperSkinSelectionStorage.
# selected_orphan_names' docstring in core/data_models.py for why this is a
# deliberately isolated, accepted undo gap rather than something these
# helpers are meant to close. Their job is only to be the single place that
# decides "does this name belong to the real pool VG or the orphan string",
# so callers (CoreFacade, layer_crud.flush_edit_session()) never re-derive
# that split themselves.

def read_full_pool(obj, bm=None) -> set:
    """Edit-Mode multi-select pool: real (__ssp_pool marker VG, via *bm* if
    given else a fresh bmesh.from_edit_mesh()) union orphan
    (storage.selected_orphan_names). Caller must have already confirmed
    has_temp_vgs(obj) / EDIT mode -- this does not check either."""
    if bm is None:
        import bmesh as _bm_mod
        bm = _bm_mod.from_edit_mesh(obj.data)
    pool = read_pool_names_from_bm(bm, obj)
    storage = obj.superskin_storage
    pool |= {n for n in storage.selected_orphan_names.split(",") if n}
    return pool


def write_full_pool(obj, mesh, names: set) -> None:
    """Edit-Mode multi-select pool bulk write: splits *names* by real-VG
    membership, routing real bones to write_pool_names_bm() (undo-safe) and
    orphan bones to storage.selected_orphan_names. Caller must have already
    confirmed has_temp_vgs(obj) / EDIT mode."""
    real_name_to_idx = _real_name_to_idx(obj)
    real_names = {n for n in names if n in real_name_to_idx}
    orphan_names = names - real_names
    write_pool_names_bm(obj, mesh, real_names)
    storage = obj.superskin_storage
    storage.selected_orphan_names = (
        f",{','.join(sorted(orphan_names))}," if orphan_names else ""
    )


def toggle_full_pool(obj, mesh, name: str, in_pool: bool) -> bool:
    """Edit-Mode single-name pool toggle: pool_set_bone_bm() for a real
    bone, falling back to a storage.selected_orphan_names set add/discard
    for an orphan/out-of-range one. Returns True if the pool actually
    changed (matches add_vg_selected()/remove_vg_selected()'s prior
    per-branch contract), False on a no-op (name already in/out of the
    pool). Caller must have already confirmed has_temp_vgs(obj) / EDIT
    mode."""
    if pool_set_bone_bm(obj, mesh, name, in_pool):
        return True
    storage = obj.superskin_storage
    names = {n for n in storage.selected_orphan_names.split(",") if n}
    if in_pool:
        if name in names:
            return False
        names.add(name)
    else:
        if name not in names:
            return False
        names.discard(name)
    storage.selected_orphan_names = f",{','.join(sorted(names))}," if names else ""
    return True
