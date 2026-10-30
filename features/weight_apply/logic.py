
from itertools import chain

import numpy as np
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



SHARPEN_REGION_HOPS = 8


_NO_XYZ = np.empty(0, dtype=np.float64)


def vertex_normals_flat(core_facade, count):
    mesh = getattr(core_facade.get_obj(), "data", None)
    try:
        if mesh is None or len(mesh.vertices) != count:
            return _NO_XYZ
        flat = np.empty(count * 3, dtype=np.float32)
        mesh.vertex_normals.foreach_get("vector", flat)
    except (AttributeError, RuntimeError):
        return _NO_XYZ
    return flat.astype(np.float64)


def posed_geometry_flat(core_facade, count):
    import bpy
    obj = core_facade.get_obj()
    try:
        eval_obj = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = eval_obj.to_mesh()
    except (AttributeError, RuntimeError, ReferenceError):
        return _NO_XYZ, _NO_XYZ
    try:
        if mesh is None or len(mesh.vertices) != count:
            return _NO_XYZ, _NO_XYZ
        co = np.empty(count * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        nor = np.empty(count * 3, dtype=np.float32)
        mesh.vertex_normals.foreach_get("vector", nor)
    finally:
        eval_obj.to_mesh_clear()
    return co.astype(np.float64), nor.astype(np.float64)


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


def expand_hops_csr(offsets, targets, seeds, hops):
    reached = np.zeros(len(offsets) - 1, dtype=np.bool_)
    frontier = np.unique(np.asarray(list(seeds), dtype=np.int64))
    reached[frontier] = True
    for _ in range(hops):
        starts = offsets[frontier]
        counts = offsets[frontier + 1] - starts
        total = int(counts.sum())
        if not total:
            break
        index = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts) + np.repeat(starts, counts)
        found = targets[index]
        frontier = np.unique(found[~reached[found]])
        if not len(frontier):
            break
        reached[frontier] = True
    return np.flatnonzero(reached)


def brush_sharpen_target(layer_dict, mask_dict, coords_flat, normals_flat, posed_flat, csr, locks,
                         is_mask_mode, mask_default, *, rust):
    vert_ids, bone_ids, weights_flat = CoreFacade.layer_to_coo(layer_dict)
    return rust.call(
        "rust_brush_sharpen_target",
        vert_ids, bone_ids, weights_flat, mask_dict, coords_flat, normals_flat, *posed_flat, *csr,
        locks, is_mask_mode, mask_default,
    )


def linked_average(layer_dict, mask_dict, links, is_mask_mode, mask_default):
    if is_mask_mode:
        mask = dict(mask_dict)
        for v, others in links.items():
            group = (v, *others)
            mask[v] = sum(mask_dict.get(u, mask_default) for u in group) / len(group)
        return layer_dict, mask
    layer = dict(layer_dict)
    for v, others in links.items():
        group = (v, *others)
        total = {}
        for u in group:
            for bone, w in layer_dict.get(u, {}).items():
                total[bone] = total.get(bone, 0.0) + w
        layer[v] = {bone: w / len(group) for bone, w in total.items()}
    return layer, mask_dict


def blend_toward_target(target, verts, amounts):
    out_v, out_b, out_w, res_mask = target.blend(verts, amounts)
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask


def apply_sharpen_scoped(layer_dict, mask_dict, selected_verts, mesh_flat, locks, steps, is_mask_mode,
                         hops=SHARPEN_REGION_HOPS, mask_default=1.0, *, rust, banding=None):
    vert_ids, bone_ids, weights_flat = CoreFacade.layer_to_coo(layer_dict)
    out_v, out_b, out_w, res_mask = rust.call(
        "rust_boundary_sharpen_scoped_passes_flat",
        vert_ids, bone_ids, weights_flat, mask_dict, np.asarray(selected_verts, dtype=np.int64),
        *mesh_flat, locks, steps, is_mask_mode, hops, mask_default,
        **_banding_kwargs(banding),
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask



def smooth_reference_edges(coords, neighbors):
    floor = SMOOTH_MIN_EDGES / SMOOTH_FULL_RADIUS
    pts = np.asarray(coords, dtype=np.float64).reshape(-1, 3)
    edge = median_edge_length(pts, neighbors) if len(pts) and neighbors else 0.0
    if edge <= 1e-12:
        return floor
    return max(float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0))) / edge, floor)


def median_edge_length(coords, neighbors):
    pts = np.asarray(coords, dtype=np.float64).reshape(-1, 3)
    heads = np.fromiter(neighbors.keys(), dtype=np.int64, count=len(neighbors))
    counts = np.fromiter(map(len, neighbors.values()), dtype=np.int64, count=len(neighbors))
    tails = np.fromiter(chain.from_iterable(neighbors.values()), dtype=np.int64, count=int(counts.sum()))
    heads = np.repeat(heads, counts)
    keep = (heads < tails) & (tails < len(pts))
    if not keep.any():
        return 0.0
    return float(np.median(np.linalg.norm(pts[heads[keep]] - pts[tails[keep]], axis=1)))


def smooth_radius(amount, reference_edges):
    return max(0.0, amount) * SMOOTH_FULL_RADIUS * reference_edges


SMOOTH_FULL_RADIUS = 0.03
SMOOTH_MIN_EDGES = 2.0


def apply_implicit_smooth(layer_dict, mask_dict, selected_verts, coords, neighbors, radii,
                          vertex_groups_lock, affected_only, is_mask_mode, mask_default=1.0,
                          *, rust, selected_band=None, links=None):
    vert_ids, bone_ids, weights = CoreFacade.layer_to_coo(layer_dict)
    out_v, out_b, out_w, res_mask = rust.call(
        "rust_implicit_smooth",
        vert_ids, bone_ids, weights, mask_dict, list(selected_verts), coords, neighbors,
        list(radii), vertex_groups_lock, affected_only, is_mask_mode, mask_default,
        selected_band=selected_band, links=links,
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask


def apply_implicit_smooth_csr(layer_dict, mask_dict, selected_verts, coords_flat, offsets, targets,
                              radii, vertex_groups_lock, affected_only, is_mask_mode, mask_default=1.0,
                              *, rust, selected_band=None, links=None):
    vert_ids, bone_ids, weights = CoreFacade.layer_to_coo(layer_dict)
    out_v, out_b, out_w, res_mask = rust.call(
        "rust_implicit_smooth_csr",
        vert_ids, bone_ids, weights, mask_dict, np.asarray(selected_verts, dtype=np.int64),
        coords_flat, offsets, targets, list(radii), vertex_groups_lock, affected_only, is_mask_mode,
        mask_default, selected_band=selected_band, links=links,
    )
    return CoreFacade.coo_to_layer(out_v, out_b, out_w), res_mask


def weld_members(welds):
    members = {}
    for v, rep in welds.items():
        members.setdefault(rep, []).append(v)
    return members


def weld_closure(welds, members, verts):
    out = set(verts)
    for v in verts:
        rep = welds.get(v)
        if rep is not None:
            out.update(members[rep])
    return out


def _mean_row(rows):
    out = {}
    for row in rows:
        for b, w in row.items():
            out[b] = out.get(b, 0.0) + w
    return {b: w / len(rows) for b, w in out.items()}


def apply_welded_smooth(welds, all_coords, all_neighbors, layer_dict, mask_dict, selected_verts, radii,
                        vertex_groups_lock, affected_only, is_mask_mode, mask_default=1.0,
                        *, rust, selected_band=None, links=None, closes=None, eased=None):
    members = weld_members(welds)

    def rep(v):
        return welds.get(v, v)

    def group(r):
        return members.get(r, (r,))

    sel, band, slot = [], [], {}
    for i, v in enumerate(selected_verts):
        r = rep(v)
        b = selected_band[i] if selected_band is not None else 0
        if r in slot:
            if radii[b] > radii[band[slot[r]]]:
                band[slot[r]] = b
        else:
            slot[r] = len(sel)
            sel.append(r)
            band.append(b)
    neighbors = {}
    for r in sel:
        ring = {rep(u) for m in group(r) for u in all_neighbors.get(m, ())}
        ring.discard(r)
        neighbors[r] = sorted(ring)
    welded_links = None
    if links:
        merged = {}
        for v, partners in links.items():
            r = rep(v)
            if r in slot:
                merged.setdefault(r, set()).update(rep(u) for u in partners)
        welded_links = {r: sorted(ps - {r}) for r, ps in merged.items() if ps - {r}} or None
    ids = set(sel).union(*neighbors.values())
    if welded_links:
        ids = ids.union(*welded_links.values())
    rows, masks = {}, {}
    for r in ids:
        present = [m for m in group(r) if m in layer_dict]
        if present:
            rows[r] = _mean_row([layer_dict[m] for m in present])
        in_mask = [mask_dict[m] for m in group(r) if m in mask_dict]
        if in_mask:
            masks[r] = sum(in_mask) / len(in_mask)
    coords = {r: all_coords[r] for r in ids}
    out_layer, out_mask = apply_implicit_smooth(
        rows, masks, sel, coords, neighbors, radii, vertex_groups_lock, affected_only,
        is_mask_mode, mask_default, rust=rust,
        selected_band=band if selected_band is not None else None, links=welded_links,
    )
    chosen = set(selected_verts)
    layer_res, mask_res = {}, {}
    for r in sel:
        k = 1.0 if closes is None else min(1.0, max(0.0, closes[band[slot[r]]]))
        if is_mask_mode:
            mask = out_mask.get(r, masks.get(r))
            row = None
        else:
            row = out_layer.get(r, rows.get(r))
            mask = None
        easing = eased is not None and k < 1.0 and len(group(r)) > 1
        for m in group(r):
            if m not in chosen:
                continue
            if row is not None:
                layer_res[m] = dict(row)
                if easing:
                    eased[m] = _ease_member(layer_dict.get(m, {}), rows.get(r, {}), row, k)
            if mask is not None:
                mask_res[m] = mask
                if easing:
                    own = mask_dict.get(m, mask_default)
                    eased[m] = min(1.0, max(0.0, own + mask - masks.get(r, own) + k * (masks.get(r, own) - own)))
    return layer_res, mask_res


def _ease_member(own, before, after, k):
    if not own:
        return dict(after)
    keys = set(own) | set(before) | set(after)
    row = {b: max(0.0, own.get(b, 0.0) + after.get(b, 0.0) - before.get(b, 0.0)
                  + k * (before.get(b, 0.0) - own.get(b, 0.0))) for b in keys}
    total = sum(row.values())
    scale = sum(own.values()) / total if total > 1e-12 else 0.0
    return {b: w * scale for b, w in row.items() if w * scale > 1e-9}
