"""Weight-apply logic — Rust Accelerated Multi-OS Portal.

Merged from simple_ops_logic.py and smooth_logic.py.
Contains normalization helpers plus add, scale, sharpen, and smooth operations.

All functions operate on Integer Bone IDs. UIController guarantees int-keyed
dicts before these functions are invoked.
"""

from collections import deque

from mathutils import Vector

from ...core.facade import CoreFacade


# ═══════════════════════════════════════════════════════════════════════════
#  Normalization helpers
# ═══════════════════════════════════════════════════════════════════════════

def _call_norm_rust(gateway_tag: str, fn_name: str, v_weights, *args):
    """Call a Rust normalization function and update *v_weights* in-place with the result."""
    rust = CoreFacade.get_rust_gateway(gateway_tag)
    result = rust.call(fn_name, v_weights, *args)
    v_weights.clear()
    v_weights.update(result)
    return v_weights


def normalize_around_active(v_weights, active_vg_id, locks, active_layer_idx=0):
    """Normalize so unlocked weights sum to 1.0 - lock_total using Integer Bone IDs.

    Args:
        v_weights: ``{bone_id: float}`` — mutable, updated in-place.
        active_vg_id: the integer bone ID that was just changed.
        locks: ``{bone_id: bool}`` — True when the group is locked.
        active_layer_idx: layer index (0 = base).
    """
    return _call_norm_rust(
        "norm_around_active",
        "rust_norm_around_active",
        v_weights,
        active_vg_id,
        locks,
        active_layer_idx,
    )


def normalize_all_unlocked(v_weights, locks):
    """Scale every unlocked weight proportionally so they sum to 1.0 - lock_total."""
    return _call_norm_rust("norm_all_unlocked", "rust_norm_all_unlocked", v_weights, locks)


# ═══════════════════════════════════════════════════════════════════════════
#  Add
# ═══════════════════════════════════════════════════════════════════════════

def apply_add(layer_dict, mask_dict, selected_verts, active_vg_id, intensity,
              vertex_groups_lock, active_layer_idx, is_mask_mode):
    """Add weight to the active bone on selected vertices using Integer Bone IDs."""
    rust = CoreFacade.get_rust_gateway("add_logic")
    return rust.call(
        "rust_add_logic",
        layer_dict,
        mask_dict,
        selected_verts,
        active_vg_id,
        intensity,
        vertex_groups_lock,
        active_layer_idx,
        is_mask_mode,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Scale
# ═══════════════════════════════════════════════════════════════════════════

# Bones within this fraction of the single closest distance are all treated
# as "nearest" (e.g. a vertex sitting between two joints redistributes to
# both instead of only the marginally-closer one).
NEAREST_BONE_TOLERANCE = 0.15

# How many mesh-adjacency hops out from the selection `compute_nearest_bones()`
# looks for topological corroboration of a spatially-nearest bone (see its
# docstring). A small fixed radius -- this is a coarse "does this bone exist
# anywhere near here at all" existence check, not a diffusion computation, so
# it deliberately doesn't share Smooth Across Surface's geodesic-radius
# machinery (that one is tied to a user preference unrelated to Scale).
TOPOLOGY_CHECK_HOPS = 3

# Weight threshold for "this bone has real weight here" in the topology
# check -- matches `_WEIGHT_EPSILON` used elsewhere in the codebase (e.g.
# `vertices_with_weight()` / the boundary-select operator) for numeric
# consistency, not an independently-chosen value.
TOPOLOGY_WEIGHT_EPSILON = 0.001


def _get_armature(obj):
    """Feature-owned armature lookup (mirrors `bone_picker/ops.py::_get_armature` --
    duplicated rather than imported, per this project's "Zero Cross-Imports"
    rule for `features/`)."""
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object:
            return mod.object
    return None


def _point_segment_dist_3d(p, a, b):
    """Perpendicular distance from 3D point *p* to segment *a*-*b* (mathutils
    Vectors). Same clamped-projection formula as `bone_picker/ops.py::
    _seg_dist_2d`, generalized from 2D screen-space picking to a real 3D
    spatial query -- no viewport projection involved."""
    ab = b - a
    denom = ab.dot(ab)
    if denom < 1e-12:
        return (p - a).length
    t = max(0.0, min(1.0, (p - a).dot(ab) / denom))
    return (p - (a + t * ab)).length


def compute_nearest_bones(core_facade, selected_verts, candidate_bone_ids, id_to_bone,
                          layer_int=None, tolerance=NEAREST_BONE_TOLERANCE):
    """For each vertex in *selected_verts*, find which of *candidate_bone_ids*
    sit closest to it in 3D world space, measured as perpendicular distance
    to each bone's head-tail segment. *candidate_bone_ids* must already be
    filtered to unlocked, non-active, deform-only bones -- see
    `WeightApplyFeature.apply_action()`'s `scale` branch, which enforces that
    scope before calling this.

    *layer_int* (optional -- the full, current active-layer weight dict,
    `{v_idx: {bone_id: weight}}`, e.g. `ctx["layer_int"]` from
    `snapshot_context()`) adds a topology corroboration pass on top of raw
    3D distance: straight-line distance alone can pick a bone that is only
    close in space but not actually mesh-connected to this area (two
    fingers touching, an ear near the head, opposite sides of a thin flap).
    When given, the spatial nearest-set computed below is filtered, per
    vertex, to bones that also have real weight (> `TOPOLOGY_WEIGHT_EPSILON`)
    somewhere within `TOPOLOGY_CHECK_HOPS` mesh-adjacency hops of the
    selection (`_expand_hops()` -- the same BFS Sharpen already uses to
    widen its own `dirty_verts`, computed once for the whole selection, not
    per vertex, since a brush stroke is normally a single contiguous
    region). If topology would eliminate every spatial candidate for a
    given vertex (e.g. a totally unpainted mesh area, so nothing has any
    topological presence yet), the filter is skipped for that vertex and
    the plain spatial nearest-set is kept -- topology only ever narrows an
    existing candidate set, it never blocks Scale outright.

    Returns `{v_idx: [bone_id, ...]}`, restricted to vertices with at least
    one candidate in range. Returns `{}` if no armature is found or there
    are no candidates -- `rust_scale_logic` treats a missing/empty entry as
    "fall back to the original whole-vertex/whole-armature redistribution",
    so this is a safe, silent no-op rather than a crash.
    """
    obj = core_facade.get_obj()
    armature = _get_armature(obj)
    if armature is None or not candidate_bone_ids:
        return {}

    segments = []
    for b_id in candidate_bone_ids:
        bone_name = id_to_bone.get(b_id)
        pose_bone = armature.pose.bones.get(bone_name) if bone_name else None
        if pose_bone is None:
            continue
        head = armature.matrix_world @ pose_bone.head
        tail = armature.matrix_world @ pose_bone.tail
        segments.append((b_id, head, tail))
    if not segments:
        return {}

    coords = core_facade.get_vertex_coordinates()
    mat = obj.matrix_world
    result = {}
    for v_idx in selected_verts:
        if v_idx >= len(coords):
            continue
        p = mat @ Vector(coords[v_idx])
        dists = [(b_id, _point_segment_dist_3d(p, head, tail)) for b_id, head, tail in segments]
        min_dist = min(d for _, d in dists)
        cutoff = min_dist * (1.0 + tolerance)
        nearest = [b_id for b_id, d in dists if d <= cutoff]
        if nearest:
            result[v_idx] = nearest

    if result and layer_int:
        vicinity = _expand_hops(core_facade, selected_verts, TOPOLOGY_CHECK_HOPS)
        bones_present = set()
        for v_idx in vicinity:
            for b_id, w in layer_int.get(v_idx, {}).items():
                if w > TOPOLOGY_WEIGHT_EPSILON:
                    bones_present.add(b_id)
        for v_idx, nearest in result.items():
            corroborated = [b_id for b_id in nearest if b_id in bones_present]
            if corroborated:
                result[v_idx] = corroborated
            # else: no spatial candidate has any topological presence nearby
            # (e.g. unpainted area) -- keep the raw spatial nearest-set as-is.

    return result


def apply_scale(layer_dict, mask_dict, selected_verts, active_vg_id, intensity,
                vertex_groups_lock, is_mask_mode, nearest_bone_ids=None):
    """Scale the active bone weight on selected vertices using Integer Bone IDs.

    *nearest_bone_ids* (`{v_idx: [bone_id, ...]}`, from `compute_nearest_bones()`
    above) scopes freed-weight redistribution to spatially nearest bones only
    -- see `rust_scale_logic`'s own comments and `features/weight_apply/
    README.md`'s "Scale's Redistribution Target Scope" for the full algorithm.
    `None`/omitted (e.g. mask mode, which never reaches this branch of the
    Rust function) is normalized to `{}` since the FFI parameter is required.
    """
    rust = CoreFacade.get_rust_gateway("scale_logic")
    return rust.call(
        "rust_scale_logic",
        layer_dict,
        mask_dict,
        selected_verts,
        active_vg_id,
        intensity,
        vertex_groups_lock,
        is_mask_mode,
        nearest_bone_ids or {},
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Sharpen — Rust-backed full-vector, cotangent-weighted, dead-zone-gated
#  contrast engine (see `rust_logic/src/sharpen_logic.rs` and
#  `features/weight_apply/README.md`'s Sharpen sections for the full design
#  rationale — the diffusion/weighting/gating algorithm itself now lives
#  entirely in the compiled Rust binary, not here)
# ═══════════════════════════════════════════════════════════════════════════

# How many hops of 1-ring diffusion Rust's low-pass reference reads, and how
# far `expand_sharpen_dirty_verts()` below must widen `dirty_verts` to match.
SHARPEN_DIFFUSION_PASSES = 8

# Dead-zone half-width (weight units) for Rust's contrast-gating step. `0.0`
# disables gating entirely (every diff passes through unchanged).
SHARPEN_DEADZONE = 0.0


def _expand_hops(core_facade, target_verts, passes):
    """Expand *target_verts* outward by up to *passes* topological hops of
    PLAIN 1-ring adjacency, returning the full reachable set (including the
    originals). This is pure vertex-set bookkeeping, not part of Sharpen's
    algorithm itself (that lives in Rust now) — it exists purely so
    `apply_action()`'s `dirty_verts` widening for Sharpen matches how far
    Rust's internal diffusion reads, so every vertex Rust's low-pass
    reference needs is actually present in the payload sent to it; see
    weight_apply/README.md's `dirty_verts` contract and the "untouched
    vertex's color goes black" bug class it exists to prevent.
    """
    adjacency = core_facade.get_cached_mesh_neighbors()
    working = set(target_verts)
    frontier = set(target_verts)
    for _ in range(passes):
        nxt = set()
        for v in frontier:
            nxt.update(adjacency.get(v, ()))
        nxt -= working
        working |= nxt
        frontier = nxt
    return working


def expand_sharpen_dirty_verts(core_facade, target_verts, passes=None):
    """Public wrapper of `_expand_hops()` for Sharpen's `dirty_verts` widening
    in `WeightApplyFeature.apply_action()` — see `_expand_hops()`'s docstring."""
    return _expand_hops(core_facade, target_verts,
                        SHARPEN_DIFFUSION_PASSES if passes is None else passes)


def apply_sharpen(core_facade, layer_dict, mask_dict, selected_verts,
                  locks, intensity, is_mask_mode,
                  passes=SHARPEN_DIFFUSION_PASSES, deadzone=SHARPEN_DEADZONE):
    """Sharpen the FULL per-vertex weight distribution (or the mask) on
    selected vertices via `rust_sharpen_full_vector` (`rust_logic/src/
    sharpen_logic.rs`) — cotangent-weighted 1-ring diffusion for the
    contrast reference, a hard dead-zone gate on the result, and full-vector
    renormalization (every unlocked bone, not just one) so a vertex's
    weights stay a valid partition of unity at any intensity. See that
    Rust module's doc comments, and `features/weight_apply/README.md`'s
    Sharpen sections, for why each piece of the algorithm exists — this
    Python wrapper only marshals data across the FFI boundary.
    """
    coords = core_facade.get_vertex_coordinates()
    adjacency = core_facade.get_cached_mesh_neighbors()
    rust = CoreFacade.get_rust_gateway("sharpen_logic")
    return rust.call(
        "rust_sharpen_full_vector",
        layer_dict, mask_dict, selected_verts,
        coords, adjacency, locks, intensity, is_mask_mode,
        passes, deadzone,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Smooth
# ═══════════════════════════════════════════════════════════════════════════

def apply_smooth(layer_dict, mask_dict, selected_verts, neighbors, intensity,
                 vertex_groups_lock, affected_only, is_mask_mode):
    """Smooth weights across neighbouring vertices with Integer Bone ID core stability."""
    rust = CoreFacade.get_rust_gateway("smooth_logic")
    return rust.call(
        "rust_smooth_logic",
        layer_dict, mask_dict, selected_verts, neighbors,
        intensity, vertex_groups_lock, affected_only, is_mask_mode,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Smooth Across Surface — geodesic-radius neighbor expansion
# ═══════════════════════════════════════════════════════════════════════════
#
# Plain 1-ring adjacency (`get_cached_mesh_neighbors()`) ties the smoothing
# neighborhood to hop count, which makes the effective real-world smoothing
# radius vary with local topology density. This module builds an alternate
# neighbor map keyed by an approximate surface (geodesic) distance instead,
# so the same call to `rust_smooth_logic` averages over a comparable
# real-world radius on both dense and sparse regions of the mesh. No Rust/
# core code is touched — this only changes what is passed as `neighbors`.

_SURFACE_CACHE: dict = {}
_SURFACE_CACHE_MAX_MESHES = 8


def _mesh_cache_key(core_facade, radius_multiplier, max_hops):
    """Identity key for the current mesh + radius settings, used to invalidate
    the per-vertex cache. Radius/hop settings are part of the key because
    different callers (smooth vs. sharpen) request different neighborhood
    sizes for the same mesh."""
    mesh = core_facade.get_mesh()
    return (mesh.as_pointer(), core_facade.get_num_verts(), radius_multiplier, max_hops)


def _local_radius(v_idx, coords, adjacency, radius_multiplier):
    """Approximate a physical smoothing radius from the average 1-ring edge length."""
    ring = adjacency.get(v_idx)
    if not ring:
        return 0.0
    cx, cy, cz = coords[v_idx]
    total = 0.0
    for n in ring:
        nx, ny, nz = coords[n]
        total += ((nx - cx) ** 2 + (ny - cy) ** 2 + (nz - cz) ** 2) ** 0.5
    return (total / len(ring)) * radius_multiplier


def _bfs_within_radius(v_idx, coords, adjacency, radius, max_hops):
    """Walk the 1-ring adjacency graph, accumulating edge length as an approximate
    geodesic distance, and collect every vertex reachable within *radius*."""
    visited = {v_idx: 0.0}
    queue = deque([(v_idx, 0.0, 0)])
    result = []
    while queue:
        cur, dist, hops = queue.popleft()
        if cur != v_idx:
            result.append(cur)
        if hops >= max_hops:
            continue
        cx, cy, cz = coords[cur]
        for nb in adjacency.get(cur, ()):
            nx, ny, nz = coords[nb]
            edge_len = ((nx - cx) ** 2 + (ny - cy) ** 2 + (nz - cz) ** 2) ** 0.5
            new_dist = dist + edge_len
            if new_dist > radius:
                continue
            if nb in visited and visited[nb] <= new_dist:
                continue
            visited[nb] = new_dist
            queue.append((nb, new_dist, hops + 1))
    return result


def build_surface_neighbors(core_facade, target_verts, radius_multiplier=3.0, max_hops=8):
    """Build a `{v_idx: [neighbor_idx, ...]}` map sized by surface distance rather
    than hop count, in the same shape `rust_smooth_logic` already expects for its
    `neighbors` argument. Results are cached per vertex for the lifetime of the
    current mesh identity, since the map only depends on topology/coordinates.

    Radius here is per-vertex (each vertex's own 1-ring average edge length,
    via `_local_radius()`) — this is Smooth Across Surface's own codepath.
    Sharpen no longer uses this at all: it needs a *contrast* reference, not
    a smoothing target, and a geodesic-ball neighbor query (this function's
    approach) turned out to sample asymmetrically near curved surfaces
    (joints/bends), which the contrast formula amplified into inconsistent
    results across a single uniform loop. Sharpen's own diffusion/weighting
    now lives entirely in `rust_logic/src/sharpen_logic.rs` — see
    `apply_sharpen()` above and `features/weight_apply/README.md`'s Sharpen
    sections.
    """
    key = _mesh_cache_key(core_facade, radius_multiplier, max_hops)
    if key not in _SURFACE_CACHE and len(_SURFACE_CACHE) >= _SURFACE_CACHE_MAX_MESHES:
        _SURFACE_CACHE.clear()
    cache = _SURFACE_CACHE.setdefault(key, {})

    missing = [v for v in target_verts if v not in cache]
    if missing:
        coords = core_facade.get_vertex_coordinates()
        adjacency = core_facade.get_cached_mesh_neighbors()
        for v in missing:
            radius = _local_radius(v, coords, adjacency, radius_multiplier)
            cache[v] = (
                _bfs_within_radius(v, coords, adjacency, radius, max_hops)
                if radius > 0 else list(adjacency.get(v, ()))
            )

    return {v: cache[v] for v in target_verts}
