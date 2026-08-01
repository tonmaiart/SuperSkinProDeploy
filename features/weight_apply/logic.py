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
              vertex_groups_lock, active_layer_idx, is_mask_mode, *, rust=None):
    """Add weight to the active bone on selected vertices using Integer Bone IDs.

    ``layer_dict`` crosses the FFI boundary as flat COO arrays
    (`CoreFacade.layer_to_coo()`/`coo_to_layer()`) instead of a nested dict --
    bone IDs here are already the final int vertex-group indices, so no
    name->id remap is needed. `mask_dict` stays a plain dict (single-level,
    far smaller than the nested layer dict, not worth flattening).

    `rust`: optional pre-built `RustWeightEngine` gateway (from
    `CoreFacade.get_rust_gateway("add_logic")`). `None` (default) constructs
    one internally, same as before -- pass one in when calling from a
    background thread (`WeightApplyFeature._dispatch_compute()`), since
    constructing a gateway reads license prefs via `bpy.context`
    (`RustWeightEngine.__init__`) and must happen on the main thread first.
    """
    vert_ids, bone_ids, weights = CoreFacade.layer_to_coo(layer_dict)
    rust = rust if rust is not None else CoreFacade.get_rust_gateway("add_logic")
    out_v, out_b, out_w, res_mask = rust.call(
        "rust_add_logic",
        vert_ids,
        bone_ids,
        weights,
        mask_dict,
        selected_verts,
        active_vg_id,
        intensity,
        vertex_groups_lock,
        active_layer_idx,
        is_mask_mode,
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask


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


def compute_chain_bone_ids(core_facade, active_vg_id, id_to_bone, deform_bone_ids):
    """Return the set of bone IDs on the SAME position-reconstructed hierarchy
    chain as *active_vg_id* -- every ancestor, every descendant, and the
    active bone itself -- scoped to *deform_bone_ids*. A bone reachable only
    through a sibling branch (e.g. a different finger sharing the same
    parent bone) is excluded, which is what stops Scale's redistribution
    from jumping across unrelated branches of the skeleton.

    Uses the EXACT SAME algorithm and bone-name set the Deform Bones list
    itself uses to compute its own display order
    (`core_subsystems/topology_cache_manager/proximity_analyzer.py`'s
    `ProximityAnalyzer._harvest_bone_raw_data()`/`compute_bone_display_order()`,
    ultimately `bone_analyzer::build_chains_internally()` in Rust) -- a
    position-based reconstruction from each bone's REST-pose `head_local`/
    `tail_local`, not the armature's real `bone.parent` chain. Many rigs
    aren't authored as a literal parent-per-finger chain, so reusing the same
    reconstruction the user already sees in the Deform Bones list is the only
    source that stays consistent with what they expect there -- a second,
    independently-reimplemented hierarchy walk here could silently disagree
    with it. Duplicated (not imported) per this project's "Zero Cross-Imports"
    rule for `features/` -- mirrors `_get_armature()` above, and the way
    `ProximityAnalyzer._harvest_bone_raw_data()` itself is the sole other
    caller of this same Rust function family.

    Returns `None` (never an empty set) when no chain data is available (no
    armature, active bone not found, fewer than two candidate bones) --
    callers must treat `None` as "no chain restriction", never as "empty
    candidate set", mirroring how `compute_nearest_bones()`'s topology
    corroboration never blocks Scale outright when it can't determine
    anything.
    """
    active_name = id_to_bone.get(active_vg_id)
    if not active_name:
        return None

    obj = core_facade.get_obj()
    armature = _get_armature(obj)
    if armature is None:
        return None

    name_to_id = {}
    bone_raw_data = []
    for b_id in deform_bone_ids:
        name = id_to_bone.get(b_id)
        bone = armature.data.bones.get(name) if name else None
        if bone is None:
            continue
        name_to_id[name] = b_id
        h, t = bone.head_local, bone.tail_local
        bone_raw_data.append((name, (h.x, h.y, h.z), (t.x, t.y, t.z)))

    if active_name not in name_to_id or len(bone_raw_data) < 2:
        return None

    rust = core_facade.get_rust_gateway("bone_chain_members")
    chain_names = rust.call("rust_compute_bone_chain_members", bone_raw_data, active_name)
    if not chain_names:
        return None

    return {name_to_id[n] for n in chain_names if n in name_to_id}


def compute_nearest_bones(core_facade, selected_verts, candidate_bone_ids, id_to_bone,
                          layer_int=None, tolerance=NEAREST_BONE_TOLERANCE):
    """For each vertex in *selected_verts*, find which of *candidate_bone_ids*
    sit closest to it in 3D world space, measured as perpendicular distance
    to each bone's head-tail segment. *candidate_bone_ids* must already be
    filtered to unlocked, non-active, deform-only bones on the SAME hierarchy
    chain as the active bone -- see `WeightApplyFeature.apply_action()`'s
    `scale` branch (and `compute_chain_bone_ids()` above), which enforces
    that scope before calling this.

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
                vertex_groups_lock, is_mask_mode, nearest_bone_ids=None, *, rust=None):
    """Scale the active bone weight on selected vertices using Integer Bone IDs.

    *nearest_bone_ids* (`{v_idx: [bone_id, ...]}`, from `compute_nearest_bones()`
    above) scopes freed-weight redistribution to spatially nearest bones only
    -- see `rust_scale_logic`'s own comments and `features/weight_apply/
    README.md`'s "Scale's Redistribution Target Scope" for the full algorithm.
    `None`/omitted (e.g. mask mode, which never reaches this branch of the
    Rust function) is normalized to `{}` since the FFI parameter is required.

    ``layer_dict`` crosses the FFI boundary as flat COO arrays (same
    convention as `apply_add()` above) instead of a nested dict.

    `rust`: optional pre-built gateway -- see `apply_add()`'s docstring.
    """
    vert_ids, bone_ids, weights = CoreFacade.layer_to_coo(layer_dict)
    rust = rust if rust is not None else CoreFacade.get_rust_gateway("scale_logic")
    out_v, out_b, out_w, res_mask = rust.call(
        "rust_scale_logic",
        vert_ids,
        bone_ids,
        weights,
        mask_dict,
        selected_verts,
        active_vg_id,
        intensity,
        vertex_groups_lock,
        is_mask_mode,
        nearest_bone_ids or {},
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask


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


def apply_sharpen(layer_dict, mask_dict, selected_verts, coords, adjacency,
                  locks, intensity, is_mask_mode,
                  passes=SHARPEN_DIFFUSION_PASSES, deadzone=SHARPEN_DEADZONE,
                  *, rust=None):
    """Sharpen the FULL per-vertex weight distribution (or the mask) on
    selected vertices via `rust_sharpen_full_vector` (`rust_logic/src/
    sharpen_logic.rs`) — cotangent-weighted 1-ring diffusion for the
    contrast reference, a hard dead-zone gate on the result, and full-vector
    renormalization (every unlocked bone, not just one) so a vertex's
    weights stay a valid partition of unity at any intensity. See that
    Rust module's doc comments, and `features/weight_apply/README.md`'s
    Sharpen sections, for why each piece of the algorithm exists — this
    Python wrapper only marshals data across the FFI boundary.

    `coords`/`adjacency` are caller-supplied rather than fetched internally
    via `core_facade` -- same reasoning as `apply_smooth()`'s own `coords`
    parameter (see its docstring): `apply_action()`'s `_run_compound_passes()`
    calls this function once per compound pass (up to `_COMPOUND_MAX_PASSES
    + 1` times per gesture tick), and `core_facade.get_vertex_coordinates()`
    is a full mesh-vertex-count read -- fetching it fresh on every pass was
    the exact redundant-refetch pattern already fixed for Smooth. Unlike
    Smooth's `smooth_coords`, `coords` here is still passed as the whole-mesh
    list, not scoped to `dirty_verts` -- out of scope for this pass, see
    `rust_logic/src/lib.rs`'s `rust_sharpen_full_vector` comment.

    `layer_dict` crosses the FFI boundary as flat COO arrays
    (`CoreFacade.layer_to_coo()`/`coo_to_layer()`, same convention as
    `apply_add()`/`apply_scale()`/`apply_smooth()`) instead of a nested dict.
    `mask_dict` stays a plain dict.

    `rust`: optional pre-built gateway -- see `apply_add()`'s docstring.
    """
    vert_ids, bone_ids, weights_flat = CoreFacade.layer_to_coo(layer_dict)
    rust = rust if rust is not None else CoreFacade.get_rust_gateway("sharpen_logic")
    out_v, out_b, out_w, res_mask = rust.call(
        "rust_sharpen_full_vector",
        vert_ids, bone_ids, weights_flat, mask_dict, selected_verts,
        coords, adjacency, locks, intensity, is_mask_mode,
        passes, deadzone,
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask


# ═══════════════════════════════════════════════════════════════════════════
#  Smooth
# ═══════════════════════════════════════════════════════════════════════════

def apply_smooth(layer_dict, mask_dict, selected_verts, coords, neighbors,
                 passes, vertex_groups_lock, affected_only, is_mask_mode, density_factors,
                 *, rust=None):
    """Smooth weights across neighbouring vertices with Integer Bone ID core
    stability. Within each vertex's fixed 1-ring, closer neighbors count for
    more via inverse-distance weighting (`rust_logic/src/smooth_logic.rs`'s
    `inverse_distance_weight()`) -- the Rust engine needs `coords` to compute
    that per-edge weight.

    `coords`/`neighbors` are caller-supplied, not fetched internally --
    `apply_action()` fetches coords exactly once per call and passes it in
    here alongside `compute_density_factors()`, instead of re-running that
    full mesh-vertex-count read.

    `coords`/`neighbors` here must already be scoped to `dirty_verts`/
    `selected_verts` (`{v_idx: value}` dicts, not whole-mesh structures) --
    `apply_action()`'s `smooth_coords`/`smooth_neighbors` build this. Rust's
    `apply_smooth_engine` only ever reads a vertex in `selected_verts` or one
    of its listed neighbors, so passing the whole mesh here would only add
    FFI marshaling cost proportional to total vertex count instead of brush
    size.

    `passes`: one intensity value per compound pass (mirrors the decomposition
    `_run_compound_passes()` used to do in Python -- `full_passes` copies of
    `1.0` plus a trailing remainder). The entire compound-pass loop now runs
    inside a single Rust call (`apply_smooth_engine`) instead of one separate
    FFI call per pass -- the previous per-pass call shape meant the *entire*
    layer/mask dict result had to round-trip back out to Python and straight
    back in as the next pass's input, which dominated profiling for a large
    selection with several compound passes.

    `layer_dict` crosses the FFI boundary as flat COO arrays
    (`CoreFacade.layer_to_coo()`/`coo_to_layer()`, same convention as
    `apply_add()`/`apply_scale()`) instead of a nested dict -- bone IDs here
    are already the final int vertex-group indices. `mask_dict` stays a
    plain dict (single-level, not worth flattening).

    `rust`: optional pre-built gateway -- see `apply_add()`'s docstring.
    """
    vert_ids, bone_ids, weights = CoreFacade.layer_to_coo(layer_dict)
    rust = rust if rust is not None else CoreFacade.get_rust_gateway("smooth_logic")
    out_v, out_b, out_w, res_mask = rust.call(
        "rust_smooth_logic",
        vert_ids, bone_ids, weights, mask_dict, selected_verts, coords, neighbors,
        passes, vertex_groups_lock, affected_only, is_mask_mode,
        density_factors,
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask


# ═══════════════════════════════════════════════════════════════════════════
#  Geodesic BFS utility — Weight Brush only
# ═══════════════════════════════════════════════════════════════════════════
#
# `_bfs_within_radius()` no longer has anything to do with Smooth (see
# `compute_density_factors()` below for Smooth's current, purely 1-ring
# density-adaptive-intensity algorithm -- the earlier geodesic/Gaussian
# neighbor-expansion design this function was originally built for has been
# fully removed). It survives here ONLY because `brush/brush_logic.py`
# imports and calls it directly (`gather_brush_vertices()`, the Weight
# Brush's Surface projection mode) for an unrelated purpose: finding which
# vertices a brush dab can physically reach along the mesh surface. Do not
# delete this function without first checking that caller.

def _bfs_within_radius(v_idx, coords, adjacency, radius, max_hops, *, with_distance=False):
    """Walk the 1-ring adjacency graph, accumulating edge length as an approximate
    geodesic distance, and collect every vertex reachable within *radius*.

    `with_distance=True` returns `[(neighbor_idx, geodesic_dist), ...]` instead
    of a plain `[neighbor_idx, ...]` list. `with_distance=False` (the
    default) is what `brush/brush_logic.py` uses, since it only needs the
    reachable vertex set, not a per-neighbor distance.

    `max_hops=None` means unbounded -- the walk stops purely on `dist >
    radius`, never on hop count. `brush/brush_logic.py` always passes its
    own explicit `_BRUSH_MAX_HOPS`.
    """
    visited = {v_idx: 0.0}
    queue = deque([(v_idx, 0.0, 0)])
    result = []
    while queue:
        cur, dist, hops = queue.popleft()
        if cur != v_idx:
            result.append((cur, dist) if with_distance else cur)
        if max_hops is not None and hops >= max_hops:
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


# ═══════════════════════════════════════════════════════════════════════════
#  Smooth's density-adaptive intensity (Density-Adaptive 1-Ring)
# ═══════════════════════════════════════════════════════════════════════════
#
# Smooth's neighbor set is plain 1-ring adjacency
# (`core_facade.get_cached_mesh_neighbors()`) -- the ORIGINAL algorithm,
# unchanged. This module previously replaced that with a geodesic-radius
# BFS + Gaussian-weighted neighbor expansion (see git history / the skill
# doc `.claude/skills/superskinpro-weight-apply-algorithm.md` for the full
# multi-round history of that approach and why it was ultimately abandoned:
# averaging over a multi-hop neighbor set on real, non-grid-uniform topology
# -- poles, tri/quad transitions, irregular valence -- produces an
# asymmetric graph Laplacian, where two mesh-adjacent vertices can end up
# with meaningfully different neighbor sets/weights and therefore visibly
# different blended results, reading as jitter/spiking along a stroke. A
# single 1-ring hop is the only neighbor set size where this is a
# mathematical non-issue (it's a discrete heat-equation step, provably
# non-oscillatory) -- so this is a deliberate return to that provably-stable
# 1-ring math, with the *diffusion rate* (intensity) made density-adaptive
# instead of the *neighbor set*.
#
# The original complaint this whole feature exists to fix (dense topology,
# e.g. a joint with many tightly-stacked loops, smooths slower/weaker per
# real-world distance than sparse topology, since 1 hop covers less real
# distance there) is addressed by SCALING intensity per vertex by how the
# local edge length compares to a reference length, instead of widening the
# neighbor set: `effective_intensity = min(intensity * (L_target / L_v),
# 1.0)` in `rust_logic/src/smooth_logic.rs`. Dense areas (`L_v` small) get a
# factor > 1, boosting their intensity to compensate; sparse areas (`L_v`
# large) get a factor < 1, damping theirs -- equalizing the *rate* smoothing
# converges at across density, without ever touching which vertices count as
# neighbors.

def compute_density_factors(coords, adjacency, target_verts):
    """Compute `{v_idx: factor}` for every vertex in `target_verts`, where
    `factor = L_target / L_v` (`L_v` = that vertex's own 1-ring AVERAGE edge
    length -- average, not median, matching this algorithm's exact spec;
    unlike the geodesic-radius design this replaced, this factor only scales
    a bounded `intensity` multiplier, not how far a search walks, so it is
    far less sensitive to a single outlier edge). `L_target` is the MEDIAN
    `L_v` across `target_verts` itself (the current selection/brush-stroke
    batch, not the whole mesh) -- median for robustness against one
    unusually dense or sparse vertex skewing the reference for the whole
    batch, and scoped to the selection so the reference stays relevant to
    what's actually being smoothed right now rather than a fixed whole-mesh
    average that could be irrelevant to the area currently under the brush.

    A vertex with no 1-ring (isolated) or zero-length edges gets `factor =
    1.0` (no adjustment -- `rust_smooth_logic` already no-ops a vertex with
    an empty neighbor list regardless of intensity, so this value is inert
    for it). Rust clamps `intensity * factor` to `1.0` per call to prevent a
    single pass from overshooting past the neighbor average -- see
    `smooth_logic.rs::apply_smooth_engine()`.

    `coords`/`adjacency` are caller-supplied (`core_facade.
    get_vertex_coordinates()`/`get_cached_mesh_neighbors()`) rather than
    fetched internally -- `apply_action()`'s `smooth` branch already needs
    both for the `apply_smooth()` Rust call itself, so this avoids a second,
    redundant full-mesh-vertex-count coordinate read on every call.
    """

    def _avg_edge_length(v):
        ring = adjacency.get(v)
        if not ring:
            return 0.0
        cx, cy, cz = coords[v]
        total = 0.0
        for n in ring:
            nx, ny, nz = coords[n]
            total += ((nx - cx) ** 2 + (ny - cy) ** 2 + (nz - cz) ** 2) ** 0.5
        return total / len(ring)

    lengths = {v: _avg_edge_length(v) for v in target_verts}
    positive = sorted(l for l in lengths.values() if l > 0)
    if not positive:
        return {v: 1.0 for v in target_verts}

    mid = len(positive) // 2
    target_length = positive[mid] if len(positive) % 2 else (positive[mid - 1] + positive[mid]) / 2.0

    return {v: (target_length / l) if l > 0 else 1.0 for v, l in lengths.items()}
