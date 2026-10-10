
import numpy as np

from ....core.facade import CoreFacade
from .. import mesh_arrays


def get_bone_centers(core_facade):
    obj = core_facade.get_obj()
    arm_obj = next(
        (m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None
    )
    centers = {}
    arm_mat = arm_obj.matrix_world if arm_obj else None
    for vg in core_facade.get_vertex_groups():
        if arm_obj and arm_mat:
            bone = arm_obj.data.bones.get(vg.name)
            if bone:
                centers[vg.name] = (
                    arm_mat @ ((bone.head_local + bone.tail_local) * 0.5)
                ).to_tuple()
    return centers


def classify_sides(core_facade, co_flat, axis_idx, direction, margin_frac=0.15):
    sides, center = core_facade.get_rust_gateway("mirror_sides").call(
        "rust_mirror_sides", co_flat, mesh_arrays.edge_verts(core_facade.get_mesh()),
        axis_idx, direction == 'POS_NEG', margin_frac,
    )
    return sides, set(center)


def generate_pairs(vg_names, bone_centers, sr_list, axis, direction):
    rust = CoreFacade.get_rust_gateway("mirror_generate_pairs")
    result = rust.call(
        "rust_mirror_generate_pairs",
        vg_names,
        bone_centers,
        sr_list,
        axis,
        direction,
    )
    return {str(k): str(v) for k, v in result.items()}


def apply(layer_dict, id_pairs, vertex_groups_lock, vertex_coords, target_side_mask, axis, direction):
    if not id_pairs:
        return layer_dict, []

    layer_dict = {k: dict(v) for k, v in layer_dict.items()}

    rust = CoreFacade.get_rust_gateway("mirror_apply")
    result, gap_v_indices = rust.call(
        "rust_mirror_apply",
        layer_dict,
        id_pairs,
        vertex_groups_lock,
        vertex_coords,
        target_side_mask,
        axis,
        direction,
    )
    return {int(k): dict(v) for k, v in result.items()}, [int(v) for v in gap_v_indices]


def apply_mask(mask_dict, vertex_coords, axis, direction):
    rust = CoreFacade.get_rust_gateway("mirror_apply_mask")
    result = rust.call(
        "rust_mirror_apply_mask",
        mask_dict,
        vertex_coords,
        axis,
        direction,
    )
    return {k: v for k, v in result.items()}


def apply_mask_flat(mask_dict: dict, vertex_coords: list,
                    axis: str, direction: str,
                    num_verts: int) -> tuple:
    rust = CoreFacade.get_rust_gateway("mirror_apply_mask_flat")
    _bridge = CoreFacade.get_flat_array_bridge()
    mask_flat = _bridge.mask_to_flat(mask_dict, num_verts, sentinel=_bridge.MASK_SENTINEL)
    res_mask_flat, gap_v_indices = rust.call(
        "rust_mirror_apply_mask_flat",
        mask_flat, vertex_coords, axis, direction,
    )
    result = _bridge.flat_to_mask(res_mask_flat, sentinel=_bridge.MASK_SENTINEL)
    return result, [int(v) for v in gap_v_indices]


_AXIS_IDX = {"X": 0, "Y": 1, "Z": 2}


def _surface_or_none(core_facade, co_flat, triangles, allowed):
    surface = mesh_arrays.surface_sampler(core_facade, co_flat, triangles, allowed)
    return surface if len(surface) else None


def build_source_side_surface(core_facade, co_flat, triangles, target_side_mask):
    allowed = np.flatnonzero(~np.asarray(target_side_mask, dtype=bool))
    return _surface_or_none(core_facade, co_flat, triangles, allowed)


def build_source_side_surface_raw(core_facade, co_flat, triangles, axis_idx, direction):
    eps = 1e-5
    vals = co_flat.reshape(-1, 3)[:, axis_idx]
    source = vals >= -eps if direction == 'POS_NEG' else vals <= eps
    return _surface_or_none(core_facade, co_flat, triangles, np.flatnonzero(source))


def _flipped_points(vertex_coords, v_indices, axis_idx):
    pts = np.array([vertex_coords[v] for v in v_indices], dtype=np.float64).reshape(-1, 3)
    pts[:, axis_idx] *= -1.0
    return np.ascontiguousarray(pts).reshape(-1)


def _fill_weight_gaps(layer_dict_before, result, gap_v_indices, id_pairs,
                       vertex_coords, axis_idx, surface, vertex_groups_lock):
    if not gap_v_indices or surface is None:
        return

    gaps = list(gap_v_indices)
    names, src_v, src_b, src_w = mesh_arrays.weights_coo(layer_dict_before, key_of=id_pairs.get)
    _found, _masks, rows, bones, weights = surface.sample(
        _flipped_points(vertex_coords, gaps, axis_idx), src_v, src_b, src_w,
        mesh_arrays.EMPTY_I, mesh_arrays.EMPTY_F, 0.0,
    )
    per_row = mesh_arrays.coo_rows(rows, bones, weights, names)

    touched = []
    for row, v_idx in enumerate(gaps):
        bone_weights = per_row.get(row)
        if not bone_weights:
            continue
        bucket = result.setdefault(v_idx, {})
        for tgt_id, w in bone_weights.items():
            bucket[tgt_id] = bucket.get(tgt_id, 0.0) + w
        touched.append(v_idx)

    if not touched:
        return

    rust = CoreFacade.get_rust_gateway("norm_all_unlocked")
    for v_idx in touched:
        v_weights = result.get(v_idx)
        if not v_weights:
            continue
        result[v_idx] = dict(rust.call("rust_norm_all_unlocked", v_weights, vertex_groups_lock))


def _symmetrize_center_vertices(result, id_pairs, center_vertices, vertex_groups_lock):
    touched = []
    for v_idx in sorted(center_vertices):
        vw = result.get(v_idx)
        if not vw:
            continue

        changed = False
        for src_id, tgt_id in id_pairs.items():
            if vertex_groups_lock.get(src_id) or vertex_groups_lock.get(tgt_id):
                continue
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


def _fill_mask_gaps(mask_dict_before, result, gap_v_indices, vertex_coords, axis_idx, surface, mask_default):
    if not gap_v_indices or surface is None:
        return

    gaps = list(gap_v_indices)
    mask_v, mask_w = mesh_arrays.mask_arrays(mask_dict_before)
    found, masks, *_ = surface.sample(
        _flipped_points(vertex_coords, gaps, axis_idx),
        mesh_arrays.EMPTY_I, mesh_arrays.EMPTY_I, mesh_arrays.EMPTY_F,
        mask_v, mask_w, mask_default,
    )
    for row, v_idx in enumerate(gaps):
        if found[row] and masks[row] > 0.0:
            result[v_idx] = masks[row]


def build_layer_mirror_plan(core_facade, axis, direction, sr_raw):
    vg_names = [vg.name for vg in core_facade.get_vertex_groups()]
    bone_centers = get_bone_centers(core_facade)
    CoreFacade.debug_log(
        "feature_domains",
        f"mirror.build_layer_mirror_plan(): sr_raw={sr_raw!r} "
        f"vg_names_sample={vg_names[:10]!r}",
    )
    name_pairs = generate_pairs(
        vg_names=vg_names,
        bone_centers=bone_centers,
        sr_list=sr_raw,
        axis=axis,
        direction=direction,
    )
    CoreFacade.debug_log(
        "feature_domains",
        f"mirror.build_layer_mirror_plan(): vg_names={len(vg_names)} "
        f"bone_centers={len(bone_centers)} name_pairs={name_pairs}",
    )

    vg_name_set = set(vg_names)
    for src, tgt in name_pairs.items():
        if src != tgt:
            continue
        for search_text, replace_text in sr_raw:
            if not search_text or not replace_text or '*' not in search_text:
                continue
            if search_text.replace('*', '') not in src:
                continue
            candidate = src.replace(search_text.replace('*', ''), replace_text.replace('*', ''))
            CoreFacade.debug_log(
                "feature_domains",
                f"mirror.build_layer_mirror_plan(): identity-pair check src={src!r} "
                f"rule=({search_text!r},{replace_text!r}) candidate={candidate!r} "
                f"present_in_vg_names={candidate in vg_name_set}",
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
    CoreFacade.debug_log(
        "feature_domains",
        f"mirror.build_layer_mirror_plan(): id_pairs={id_pairs}",
    )
    return id_pairs if id_pairs else None


def _selection_scope(core_facade):
    if not core_facade.is_paint_session():
        return None
    selected = set(int(v) for v in core_facade.get_selected_verts())
    return selected or None


def _restrict_to_scope(before, after, scope):
    if scope is None:
        return after
    out = {k: v for k, v in before.items() if int(k) not in scope}
    out.update({k: v for k, v in after.items() if int(k) in scope})
    return out


def _mirror_active_layer(core_facade, *, do_mask, do_layer, id_pairs,
                          axis, axis_idx, direction,
                          target_side_mask, fill_surface, mask_fill_surface,
                          vertex_coords, center_vertices, scope):
    if do_mask:
        mask_dict = core_facade.get_active_mask_dict()

        active_idx = core_facade.get_active_layer_index()
        mask_default = 1.0
        for m in core_facade.get_meta_list():
            if m.get("index", -1) == active_idx:
                mask_default = float(m.get("mask_default", 1.0))
                break

        res_mask, mask_gaps = apply_mask_flat(
            mask_dict=mask_dict,
            vertex_coords=vertex_coords,
            axis=axis,
            direction=direction,
            num_verts=core_facade.get_num_verts(),
        )
        if mask_gaps:
            _fill_mask_gaps(
                mask_dict, res_mask, mask_gaps, vertex_coords, axis_idx,
                mask_fill_surface, mask_default,
            )
            CoreFacade.debug_log(
                "feature_domains",
                f"mirror._mirror_active_layer(): mask gap-fill covered "
                f"{len(mask_gaps)} candidate vertex(es) on layer {active_idx}, "
                f"mask_default={mask_default}",
            )
        core_facade.write_mask_dict(_restrict_to_scope(mask_dict, res_mask, scope))

    if do_layer:
        layer_str = core_facade.read_active_layer()
        bone_to_id, id_to_bone = core_facade.get_unified_mapping()

        layer_int = {
            int(v_idx): {
                int(bone_to_id[b]): float(w)
                for b, w in weights.items()
                if b in bone_to_id
            }
            for v_idx, weights in layer_str.items()
        }
        locks_by_id = core_facade.get_locks_by_id()
        res_layer_int, weight_gaps = apply(
            layer_dict=layer_int,
            id_pairs=id_pairs,
            vertex_groups_lock=locks_by_id,
            vertex_coords=vertex_coords,
            target_side_mask=target_side_mask,
            axis=axis,
            direction=direction,
        )
        if weight_gaps:
            _fill_weight_gaps(
                layer_int, res_layer_int, weight_gaps, id_pairs,
                vertex_coords, axis_idx, fill_surface, locks_by_id,
            )
            CoreFacade.debug_log(
                "feature_domains",
                f"mirror._mirror_active_layer(): weight gap-fill covered "
                f"{len(weight_gaps)} candidate vertex(es)",
            )
        _symmetrize_center_vertices(
            res_layer_int, id_pairs, center_vertices, locks_by_id,
        )
        res_layer_int = _restrict_to_scope(layer_int, res_layer_int, scope)
        res_layer_str = {
            int(v_idx): {
                str(id_to_bone[b_id]): float(w)
                for b_id, w in weights.items()
                if b_id in id_to_bone
            }
            for v_idx, weights in res_layer_int.items()
        }
        core_facade.write_active_layer(res_layer_str)
    else:
        core_facade.finish_color_only()


def _prepare_mirror_pipeline(core_facade, axis, axis_idx, direction, sr_raw, mirror_data):
    do_mask = mirror_data in ('MASK', 'BOTH')
    do_layer = mirror_data in ('BONE', 'BOTH')

    if do_layer:
        id_pairs = build_layer_mirror_plan(core_facade, axis, direction, sr_raw)
        if id_pairs is None:
            do_layer = False
    else:
        id_pairs = None

    if not do_mask and not do_layer:
        raise ValueError("No mirror pairs found for either channel.")

    co_flat = mesh_arrays.local_co(core_facade.get_mesh())
    triangles = mesh_arrays.loop_triangles(core_facade.get_mesh())
    vertex_coords = core_facade.get_vertex_coordinates()
    if do_layer:
        target_side_mask, center_vertices = classify_sides(core_facade, co_flat, axis_idx, direction)
        fill_surface = build_source_side_surface(core_facade, co_flat, triangles, target_side_mask)
    else:
        target_side_mask, center_vertices, fill_surface = None, set(), None
    mask_fill_surface = (
        build_source_side_surface_raw(core_facade, co_flat, triangles, axis_idx, direction)
        if do_mask else None
    )

    return {
        "do_mask": do_mask,
        "do_layer": do_layer,
        "id_pairs": id_pairs,
        "target_side_mask": target_side_mask,
        "fill_surface": fill_surface,
        "mask_fill_surface": mask_fill_surface,
        "vertex_coords": vertex_coords,
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


def execute_mirror_pipeline(core_facade) -> int:
    from ..weight_toolkit_feature import MirrorPreferencesService

    axis = MirrorPreferencesService.get_mirror_axis()
    direction = MirrorPreferencesService.get_mirror_direction()
    sr_raw = MirrorPreferencesService.get_mirror_search_replace_pairs()
    mirror_data = MirrorPreferencesService.get_mirror_data()
    axis_idx = _AXIS_IDX[axis]

    CoreFacade.debug_log(
        "feature_domains",
        f"mirror.execute_mirror_pipeline(): mirror_data={mirror_data} "
        f"obj.mode={core_facade.get_obj().mode} axis={axis} direction={direction}",
    )

    prep = _prepare_mirror_pipeline(core_facade, axis, axis_idx, direction, sr_raw, mirror_data)
    prep_kwargs = dict(
        do_mask=prep["do_mask"], do_layer=prep["do_layer"], id_pairs=prep["id_pairs"],
        axis=axis, axis_idx=axis_idx, direction=direction,
        target_side_mask=prep["target_side_mask"], fill_surface=prep["fill_surface"],
        mask_fill_surface=prep["mask_fill_surface"], vertex_coords=prep["vertex_coords"],
        center_vertices=prep["center_vertices"], scope=prep["scope"],
    )

    targets = _get_multi_mirror_targets(core_facade)
    if targets is None:
        _mirror_active_layer(core_facade, **prep_kwargs)
        return 1

    original_active = core_facade.get_active_layer_index()
    for idx in targets:
        core_facade.switch_to_layer(idx)
        _mirror_active_layer(core_facade, **prep_kwargs)
    core_facade.switch_to_layer(original_active)
    return len(targets)
