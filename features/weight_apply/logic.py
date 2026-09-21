"""Weight-apply logic — Rust Accelerated Multi-OS Portal."""

from mathutils import Vector

from ...core.facade import CoreFacade



def _call_norm_rust(gateway_tag: str, fn_name: str, v_weights, *args):
    """Call a Rust normalization function and update *v_weights* in-place with the result."""
    rust = CoreFacade.get_rust_gateway(gateway_tag)
    result = rust.call(fn_name, v_weights, *args)
    v_weights.clear()
    v_weights.update(result)
    return v_weights


def normalize_around_active(v_weights, active_vg_id, locks, active_layer_idx=0):
    """Normalize so unlocked weights sum to 1.0 - lock_total using Integer Bone IDs."""
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



def apply_add(layer_dict, mask_dict, selected_verts, active_vg_id, intensity,
              vertex_groups_lock, active_layer_idx, is_mask_mode, mask_default=1.0,
              *, rust=None):
    """Add weight to the active bone on selected vertices using Integer Bone IDs."""
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
        mask_default,
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask



NEAREST_BONE_TOLERANCE = 0.15


def _get_armature(obj):
    """Feature-owned armature lookup (mirrors `bone_picker/ops.py::_get_armature`."""
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object:
            return mod.object
    return None


def _point_segment_dist_3d(p, a, b):
    """Perpendicular distance from 3D point *p* to segment *a*-*b* (mathutils Vectors)."""
    ab = b - a
    denom = ab.dot(ab)
    if denom < 1e-12:
        return (p - a).length
    t = max(0.0, min(1.0, (p - a).dot(ab) / denom))
    return (p - (a + t * ab)).length


def compute_chain_bone_ids(core_facade, active_vg_id, id_to_bone, deform_bone_ids):
    """Return the set of bone IDs on the SAME position-reconstructed hierarchy chain as
    *active_vg_id*."""
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
                          tolerance=NEAREST_BONE_TOLERANCE):
    """For each vertex in *selected_verts*, find which of *candidate_bone_ids* sit closest to
    it in 3D world space, measured as perpendicular distance to each bone's head-tail segment."""
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

    return result


def apply_scale(layer_dict, mask_dict, selected_verts, active_vg_id, intensity,
                vertex_groups_lock, is_mask_mode, nearest_bone_ids=None, mask_default=1.0,
                *, rust=None):
    """Scale the active bone weight on selected vertices using Integer Bone IDs."""
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
        mask_default,
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask



SHARPEN_DIFFUSION_PASSES = 8

SHARPEN_DEADZONE = 0.0


def _expand_hops(core_facade, target_verts, passes):
    """Expand *target_verts* outward by up to *passes* topological hops of PLAIN 1-ring
    adjacency, returning the full reachable set (including the originals)."""
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
    """Public wrapper of `_expand_hops()` for Sharpen's `dirty_verts` widening in
    `WeightApplyFeature.apply_action()`."""
    return _expand_hops(core_facade, target_verts,
                        SHARPEN_DIFFUSION_PASSES if passes is None else passes)


def apply_sharpen(layer_dict, mask_dict, selected_verts, coords, adjacency,
                  locks, intensity, is_mask_mode,
                  passes=SHARPEN_DIFFUSION_PASSES, deadzone=SHARPEN_DEADZONE,
                  mask_default=1.0, *, rust=None):
    """Sharpen the FULL per-vertex weight distribution (or the mask) on selected vertices via
    `rust_sharpen_full_vector` (`rust_logic/src/ sharpen_logic.rs`)."""
    vert_ids, bone_ids, weights_flat = CoreFacade.layer_to_coo(layer_dict)
    rust = rust if rust is not None else CoreFacade.get_rust_gateway("sharpen_logic")
    out_v, out_b, out_w, res_mask = rust.call(
        "rust_sharpen_full_vector",
        vert_ids, bone_ids, weights_flat, mask_dict, selected_verts,
        coords, adjacency, locks, intensity, is_mask_mode,
        passes, deadzone, mask_default,
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask



def apply_smooth(layer_dict, mask_dict, selected_verts, coords, neighbors,
                 passes, vertex_groups_lock, affected_only, is_mask_mode, density_factors,
                 mask_default=1.0, *, rust=None):
    """Smooth weights across neighbouring vertices with Integer Bone ID core stability."""
    vert_ids, bone_ids, weights = CoreFacade.layer_to_coo(layer_dict)
    rust = rust if rust is not None else CoreFacade.get_rust_gateway("smooth_logic")
    out_v, out_b, out_w, res_mask = rust.call(
        "rust_smooth_logic",
        vert_ids, bone_ids, weights, mask_dict, selected_verts, coords, neighbors,
        passes, vertex_groups_lock, affected_only, is_mask_mode,
        density_factors, mask_default,
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask



def compute_density_factors(coords, adjacency, target_verts):
    """Compute `{v_idx: factor}` for every vertex in `target_verts`, where `factor = L_target /
    L_v` (`L_v` = that vertex's own 1-ring AVERAGE edge length."""

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
