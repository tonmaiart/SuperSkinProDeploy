
from typing import NamedTuple

import numpy as np

from ....core.facade import CoreFacade
from .. import mesh_arrays
from . import detect


class MirrorMatches(NamedTuple):
    """Where each target vertex reads from: 3 source corners and their shares, per target."""
    targets: np.ndarray
    corners: np.ndarray
    bary: np.ndarray


def classify_sides(core_facade, co_flat, axis_idx, direction, margin_frac=0.15):
    sides, center = core_facade.get_rust_gateway("mirror_sides").call(
        "rust_mirror_sides", co_flat, mesh_arrays.edge_verts(core_facade.get_mesh()),
        axis_idx, direction == 'POS_NEG', margin_frac,
    )
    return sides, set(center)


def _centered_on_real_plane(core_facade, co_flat, axis_idx):
    offset = core_facade.get_rust_gateway("mirror_plane_offset").call(
        "rust_mirror_plane_offset", co_flat, axis_idx,
    )
    if not offset:
        return co_flat, 0.0
    centered = co_flat.copy()
    centered[axis_idx::3] -= offset
    return centered, offset


def _matches(core_facade, co_flat, triangles, target_verts, axis_idx):
    mesh = core_facade.get_mesh()
    loop_vert, loop_start, loop_total = mesh_arrays.polygons(mesh)
    targets, corners, bary, walked, exact = core_facade.get_rust_gateway("mirror_match").call(
        "rust_mirror_match", co_flat, triangles, mesh_arrays.edge_verts(mesh),
        loop_vert, loop_start, loop_total,
        np.asarray(target_verts, dtype=np.int64), axis_idx,
    )
    CoreFacade.debug_log(
        "feature_domains",
        f"mirror._matches(): {len(targets)} target(s), {walked} by topology, {exact} exact, "
        f"{len(targets) - walked - exact} blended",
    )
    return MirrorMatches(
        np.asarray(targets, dtype=np.int64),
        np.asarray(corners, dtype=np.int64),
        np.asarray(bary, dtype=np.float64),
    )


def _raw_target_verts(co_flat, axis_idx, direction):
    eps = 1e-5
    vals = co_flat.reshape(-1, 3)[:, axis_idx]
    return np.flatnonzero(vals < -eps if direction == 'POS_NEG' else vals > eps)


def apply(layer_dict, id_pairs, vertex_groups_lock, matches):
    if not id_pairs:
        return layer_dict
    return CoreFacade.get_rust_gateway("mirror_apply_matches").call(
        "rust_mirror_apply_matches", layer_dict, id_pairs, vertex_groups_lock,
        matches.targets, matches.corners, matches.bary,
    )


def apply_mask(mask_dict, matches, num_verts, mask_default):
    dense = np.full(num_verts, mask_default, dtype=np.float64)
    mask_v, mask_w = mesh_arrays.mask_arrays(mask_dict)
    in_range = (mask_v >= 0) & (mask_v < num_verts)
    dense[mask_v[in_range]] = mask_w[in_range]
    values = (dense[matches.corners.reshape(-1, 3)] * matches.bary.reshape(-1, 3)).sum(axis=1)
    result = dict(mask_dict)
    result.update(zip(matches.targets.tolist(), values.tolist()))
    return result


def _symmetrize_center_vertices(result, id_pairs, center_vertices, vertex_groups_lock):
    pairs = [(s, t) for s, t in id_pairs.items()
             if not (vertex_groups_lock.get(s) or vertex_groups_lock.get(t))]
    pairs_of = {}
    for k, (s, t) in enumerate(pairs):
        pairs_of.setdefault(s, []).append(k)
        pairs_of.setdefault(t, []).append(k)

    touched = []
    for v_idx in sorted(center_vertices):
        vw = result.get(v_idx)
        if not vw:
            continue

        bones = [b for b in vw if b in pairs_of]
        ks = set()
        while bones:
            for k in pairs_of[bones.pop()]:
                if k not in ks:
                    ks.add(k)
                    bones.extend(pairs[k])
        changed = False
        for src_id, tgt_id in (pairs[k] for k in sorted(ks)):
            w_src = vw.get(src_id, 0.0)
            w_tgt = vw.get(tgt_id, 0.0)
            if w_src == 0.0 and w_tgt == 0.0:
                continue
            half = (w_src + w_tgt) / 2.0
            if w_src != half:
                vw[src_id] = half
                changed = True
            if w_tgt != half:
                vw[tgt_id] = half
                changed = True

        if changed:
            touched.append(v_idx)

    if not touched:
        return touched

    rust = CoreFacade.get_rust_gateway("norm_all_unlocked")
    for v_idx in touched:
        v_weights = result.get(v_idx)
        if not v_weights:
            continue
        result[v_idx] = dict(rust.call("rust_norm_all_unlocked", v_weights, vertex_groups_lock))

    return touched


def build_layer_mirror_plan(core_facade, sym, direction, sr_raw):
    name_pairs, stats = detect.build_name_pairs(core_facade, sym, direction, sr_raw)
    CoreFacade.debug_log(
        "feature_domains",
        f"mirror.build_layer_mirror_plan(): axis={sym.axis} method={sym.method} "
        f"direction={direction} stats={stats} name_pairs={name_pairs}",
    )
    if not name_pairs:
        return None

    bone_to_id, id_to_bone = core_facade.get_unified_mapping()
    id_pairs = {
        bone_to_id[src]: bone_to_id[tgt]
        for src, tgt in name_pairs.items()
        if src in bone_to_id and tgt in bone_to_id
    }
    for src_id, tgt_id in list(id_pairs.items()):
        id_pairs.setdefault(tgt_id, src_id)
    return id_pairs if id_pairs else None


def _selection_scope(core_facade):
    selected = set(int(v) for v in core_facade.get_selected_verts())
    return selected or None


def _restrict_to_scope(before, after, scope):
    if scope is None:
        return after
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in before.items() if int(k) not in scope}
    out.update({k: v for k, v in after.items() if int(k) in scope})
    return out


def _mirror_layer(core_facade, layer_idx, *, do_mask, do_layer, id_pairs,
                  center_vertices, scope, weight_matches, mask_matches):
    if do_mask:
        mask_dict = core_facade.get_layer_mask_dict(layer_idx)

        mask_default = 1.0
        for m in core_facade.get_meta_list():
            if m.get("index", -1) == layer_idx:
                mask_default = float(m.get("mask_default", 1.0))
                break

        res_mask = apply_mask(mask_dict, mask_matches, core_facade.get_num_verts(), mask_default)
        core_facade.write_layer_mask_dict(layer_idx, _restrict_to_scope(mask_dict, res_mask, scope))

    if do_layer:
        layer_int = core_facade.read_layer_int(layer_idx)
        _bone_to_id, id_to_bone = core_facade.get_unified_mapping()
        locks_by_id = core_facade.get_locks_by_id(layer_idx)
        res_layer_int = apply(layer_int, id_pairs, locks_by_id, weight_matches)
        _symmetrize_center_vertices(res_layer_int, id_pairs, center_vertices, locks_by_id)
        res_layer_int = _restrict_to_scope(layer_int, res_layer_int, scope)
        core_facade.write_layer_int(layer_idx, res_layer_int, id_to_bone)


def _prepare_mirror_pipeline(core_facade, sym, direction, sr_raw, mirror_data):
    do_mask = mirror_data in ('MASK', 'BOTH')
    do_layer = mirror_data in ('BONE', 'BOTH')

    if do_layer:
        id_pairs = build_layer_mirror_plan(core_facade, sym, direction, sr_raw)
        if id_pairs is None:
            do_layer = False
    else:
        id_pairs = None

    if not do_mask and not do_layer:
        raise ValueError("No mirror pairs found for either channel.")

    axis_idx = sym.axis_idx
    co_flat, plane_offset = _centered_on_real_plane(
        core_facade, mesh_arrays.local_co(core_facade.get_mesh()), axis_idx,
    )
    CoreFacade.debug_log(
        "feature_domains", f"mirror._prepare_mirror_pipeline(): plane_offset={plane_offset}",
    )
    triangles = mesh_arrays.loop_triangles(core_facade.get_mesh())

    weight_matches = mask_matches = None
    center_vertices = set()
    if do_layer:
        target_side_mask, center_vertices = classify_sides(core_facade, co_flat, axis_idx, direction)
        weight_matches = _matches(
            core_facade, co_flat, triangles, np.flatnonzero(target_side_mask), axis_idx,
        )
    if do_mask:
        mask_matches = _matches(
            core_facade, co_flat, triangles, _raw_target_verts(co_flat, axis_idx, direction),
            axis_idx,
        )

    return {
        "do_mask": do_mask,
        "do_layer": do_layer,
        "id_pairs": id_pairs,
        "weight_matches": weight_matches,
        "mask_matches": mask_matches,
        "center_vertices": center_vertices,
        "scope": _selection_scope(core_facade),
    }


def _get_multi_mirror_targets(core_facade):
    obj = core_facade.get_obj()
    raw = obj.superskin_storage.layer_selected_indices
    selected = [int(k) for k in raw.split(",") if k] if raw else []
    group_indices = {item.index for item in obj.superskin_layers_collection if item.is_group}
    selected = sorted(i for i in selected if i not in group_indices)
    return selected if len(selected) >= 2 else None


def _get_all_mirror_targets(core_facade):
    return [int(m["index"]) for m in core_facade.get_meta_list()
            if "index" in m and not m.get("is_group")]


def resolve_direction(core_facade, sym=None):
    from ..weight_toolkit_feature import MirrorPreferencesService

    sym = sym or detect.detect_symmetry(core_facade)
    return sym, sym.direction(MirrorPreferencesService.get_mirror_source_side())


def execute_mirror_pipeline(core_facade, sym=None, all_layers=False) -> int:
    from ..weight_toolkit_feature import MirrorPreferencesService

    if not core_facade.is_paint_session():
        raise ValueError("Mirror runs only inside an Edit Layer Weight session.")
    sym, direction = resolve_direction(core_facade, sym)
    axis = sym.axis
    sr_raw = MirrorPreferencesService.get_mirror_search_replace_pairs()
    mirror_data = MirrorPreferencesService.get_mirror_data()

    CoreFacade.debug_log(
        "feature_domains",
        f"mirror.execute_mirror_pipeline(): mirror_data={mirror_data} "
        f"obj.mode={core_facade.get_obj().mode} axis={axis} method={sym.method} "
        f"direction={direction} all_layers={all_layers}",
    )

    prep = _prepare_mirror_pipeline(core_facade, sym, direction, sr_raw, mirror_data)

    if all_layers:
        targets = _get_all_mirror_targets(core_facade)
    else:
        targets = _get_multi_mirror_targets(core_facade)
    targets = targets or [core_facade.get_active_layer_index()]
    for idx in targets:
        _mirror_layer(core_facade, idx, **prep)
    core_facade.finish_color_only()
    return len(targets)
