
from mathutils import Vector

from ...core.facade import CoreFacade



def _call_norm_rust(gateway_tag: str, fn_name: str, v_weights, *args):
    rust = CoreFacade.get_rust_gateway(gateway_tag)
    result = rust.call(fn_name, v_weights, *args)
    v_weights.clear()
    v_weights.update(result)
    return v_weights


def normalize_around_active(v_weights, active_vg_id, locks, active_layer_idx=0):
    return _call_norm_rust(
        "norm_around_active",
        "rust_norm_around_active",
        v_weights,
        active_vg_id,
        locks,
        active_layer_idx,
    )


def normalize_all_unlocked(v_weights, locks):
    return _call_norm_rust("norm_all_unlocked", "rust_norm_all_unlocked", v_weights, locks)



def _banding_kwargs(banding):
    if banding is None:
        return {}
    selected_band, band_steps = banding
    return {"selected_band": selected_band, "band_steps": band_steps}


def apply_add(layer_dict, mask_dict, selected_verts, active_vg_id, intensity,
              vertex_groups_lock, active_layer_idx, is_mask_mode, mask_default=1.0,
              *, rust=None, banding=None):
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
        **_banding_kwargs(banding),
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask



NEAREST_BONE_TOLERANCE = 0.15

SCALE_DONOR_SEARCH_HOPS = 16


def _get_armature(obj):
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object:
            return mod.object
    return None


def _point_segment_dist_3d(p, a, b):
    ab = b - a
    denom = ab.dot(ab)
    if denom < 1e-12:
        return (p - a).length
    t = max(0.0, min(1.0, (p - a).dot(ab) / denom))
    return (p - (a + t * ab)).length


def compute_chain_bone_ids(core_facade, active_vg_id, id_to_bone, deform_bone_ids):
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


def _nearest_bone_shares(core_facade, verts, bone_ids, id_to_bone, coords,
                         tolerance=NEAREST_BONE_TOLERANCE):
    obj = core_facade.get_obj()
    armature = _get_armature(obj)
    if armature is None or not bone_ids:
        return {}

    segments = []
    for b_id in bone_ids:
        name = id_to_bone.get(b_id)
        bone = armature.data.bones.get(name) if name else None
        if bone is not None:
            segments.append((b_id, bone.head_local, bone.tail_local))
    if not segments:
        return {}

    to_armature = armature.matrix_world.inverted() @ obj.matrix_world
    result = {}
    for v_idx in verts:
        if v_idx >= len(coords):
            continue
        p = to_armature @ Vector(coords[v_idx])
        dists = [(b_id, _point_segment_dist_3d(p, head, tail)) for b_id, head, tail in segments]
        cutoff = min(d for _, d in dists) * (1.0 + tolerance)
        near = [(b_id, 1.0 / (d + 1e-6)) for b_id, d in dists if d <= cutoff]
        total = sum(w for _, w in near)
        result[v_idx] = [(b_id, w / total) for b_id, w in near]
    return result


def compute_scale_recipient_shares(core_facade, layer_int, coords, selected_verts,
                                   active_vg_id, candidate_ids, chain_ids, id_to_bone):
    if not selected_verts or not candidate_ids:
        return {}

    region = _expand_hops(core_facade, selected_verts, SCALE_DONOR_SEARCH_HOPS)
    adjacency = core_facade.get_cached_mesh_neighbors()
    region_adj = {v: [n for n in adjacency.get(v, ()) if n in region] for v in region}
    region_coords = {v: tuple(coords[v]) for v in region if v < len(coords)}
    vert_ids, bone_ids, weights = CoreFacade.layer_to_coo(
        {v: layer_int[v] for v in region if v in layer_int}
    )

    rust = core_facade.get_rust_gateway("scale_targets")
    shares, unresolved = rust.call(
        "rust_scale_recipient_shares",
        vert_ids, bone_ids, weights, region_adj, region_coords,
        list(selected_verts), active_vg_id if active_vg_id is not None else -1,
        list(candidate_ids), list(chain_ids) if chain_ids is not None else None,
    )
    if unresolved:
        pool = candidate_ids & chain_ids if chain_ids else candidate_ids
        shares.update(_nearest_bone_shares(
            core_facade, unresolved, pool or candidate_ids, id_to_bone, coords,
        ))
    return shares


def apply_scale(layer_dict, mask_dict, selected_verts, active_vg_id, intensity,
                vertex_groups_lock, is_mask_mode, recipient_shares=None, mask_default=1.0,
                *, rust=None, banding=None):
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
        recipient_shares or {},
        mask_default,
        **_banding_kwargs(banding),
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask



SHARPEN_DIFFUSION_PASSES = 8

SHARPEN_DEADZONE = 0.0


def _expand_hops(core_facade, target_verts, passes):
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


def expand_sharpen_region(core_facade, target_verts, passes=SHARPEN_DIFFUSION_PASSES):
    adjacency = core_facade.get_cached_mesh_neighbors()
    dirty = _expand_hops(core_facade, target_verts, passes)
    region = set(dirty)
    for v in dirty:
        region.update(adjacency.get(v, ()))
    return dirty, region


def apply_sharpen_scoped(layer_dict, mask_dict, selected_verts, region_coords, region_adjacency,
                         locks, steps, is_mask_mode, passes=SHARPEN_DIFFUSION_PASSES,
                         deadzone=SHARPEN_DEADZONE, mask_default=1.0, *, rust, banding=None):
    vert_ids, bone_ids, weights_flat = CoreFacade.layer_to_coo(layer_dict)
    out_v, out_b, out_w, res_mask = rust.call(
        "rust_sharpen_scoped_passes",
        vert_ids, bone_ids, weights_flat, mask_dict, selected_verts,
        region_coords, region_adjacency, locks, steps, is_mask_mode,
        passes, deadzone, mask_default,
        **_banding_kwargs(banding),
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask



def apply_smooth(layer_dict, mask_dict, selected_verts, coords, neighbors,
                 passes, vertex_groups_lock, affected_only, is_mask_mode, density_factors,
                 mask_default=1.0, *, rust=None, banding=None):
    vert_ids, bone_ids, weights = CoreFacade.layer_to_coo(layer_dict)
    rust = rust if rust is not None else CoreFacade.get_rust_gateway("smooth_logic")
    out_v, out_b, out_w, res_mask = rust.call(
        "rust_smooth_logic",
        vert_ids, bone_ids, weights, mask_dict, selected_verts, coords, neighbors,
        passes, vertex_groups_lock, affected_only, is_mask_mode,
        density_factors, mask_default,
        **_banding_kwargs(banding),
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask



def compute_density_factors(coords, adjacency, target_verts, length_cache=None):

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

    if length_cache is None:
        lengths = {v: _avg_edge_length(v) for v in target_verts}
    else:
        lengths = {}
        for v in target_verts:
            length = length_cache.get(v)
            if length is None:
                length = length_cache[v] = _avg_edge_length(v)
            lengths[v] = length
    positive = sorted(l for l in lengths.values() if l > 0)
    if not positive:
        return {v: 1.0 for v in target_verts}

    mid = len(positive) // 2
    target_length = positive[mid] if len(positive) % 2 else (positive[mid - 1] + positive[mid]) / 2.0

    return {v: (target_length / l) if l > 0 else 1.0 for v, l in lengths.items()}
