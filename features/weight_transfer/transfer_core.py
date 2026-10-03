
import numpy as np

from ...core.facade import CoreFacade
from ...interface.utils.utils import (
    _has_layer_system,
    _run_in_object_context,
    _select_only_layer,
    _enforce_visualizer_from_tab_state,
    sync_layers_to_ui_collection,
)


def unique_layer_name(existing_names, name):
    if name not in existing_names:
        return name
    i = 1
    while f"{name}.{i:03d}" in existing_names:
        i += 1
    return f"{name}.{i:03d}"


def ensure_armature_modifier(target, armature_obj):
    arm_mod = next((m for m in target.modifiers if m.type == 'ARMATURE'), None)
    if not arm_mod:
        arm_mod = target.modifiers.new(name="Armature", type='ARMATURE')
    arm_mod.object = armature_obj
    arm_mod.use_deform_preserve_volume = True
    return arm_mod


def ensure_native_vertex_groups(target, bone_names):
    existing_vg_names = {vg.name for vg in target.vertex_groups}
    for name in bone_names:
        if name not in existing_vg_names:
            target.vertex_groups.new(name=name)


def _flat_points(points):
    return np.array([tuple(p) for p in points], dtype=np.float64).reshape(-1)


def _sampler(positions, triangles, allowed_verts):
    allowed = None if allowed_verts is None else np.fromiter(allowed_verts, dtype=np.int64)
    return CoreFacade.get_rust_gateway("surface_logic").call(
        "rust_surface_sampler",
        _flat_points(positions),
        np.asarray(triangles, dtype=np.int64).reshape(-1),
        allowed,
    )


def build_surface(positions, triangles):
    return _sampler(positions, triangles, None)


def build_restricted_surface(positions, triangles, allowed_verts):
    surface = _sampler(positions, triangles, allowed_verts)
    return surface if len(surface) else None


def _weights_coo(layer_weights):
    slots = {}
    vs, bs, ws = [], [], []
    for v_idx, bone_weights in layer_weights.items():
        for bone, w in bone_weights.items():
            vs.append(v_idx)
            bs.append(slots.setdefault(bone, len(slots)))
            ws.append(w)
    return (
        list(slots),
        np.asarray(vs, dtype=np.int64),
        np.asarray(bs, dtype=np.int64),
        np.asarray(ws, dtype=np.float64),
    )


def _target_points(target, verts, target_positions):
    if target_positions is not None:
        return _flat_points(target_positions[v] for v in verts)
    mesh = target.data
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    m = np.array(target.matrix_world, dtype=np.float64)
    world = co.reshape(-1, 3).astype(np.float64)[verts] @ m[:3, :3].T + m[:3, 3]
    return np.ascontiguousarray(world).reshape(-1)


def closest_surface_point_transfer(
    target, source_surface, layer_weights, layer_mask, mask_default,
    allowed_target_verts=None, weight_surface=None, target_positions=None,
):
    weight_surface = source_surface if weight_surface is None else weight_surface
    verts = [
        v for v in range(len(target.data.vertices))
        if allowed_target_verts is None or v in allowed_target_verts
    ]
    if not verts:
        return {}, {}
    points = _target_points(target, np.asarray(verts, dtype=np.int64), target_positions)

    names, src_v, src_b, src_w = _weights_coo(layer_weights)
    mask_v = np.fromiter(layer_mask.keys(), dtype=np.int64, count=len(layer_mask))
    mask_w = np.fromiter(layer_mask.values(), dtype=np.float64, count=len(layer_mask))
    empty_i = np.empty(0, dtype=np.int64)
    empty_f = np.empty(0, dtype=np.float64)

    if weight_surface is source_surface:
        _found, masks, rows, bones, weights = source_surface.sample(
            points, src_v, src_b, src_w, mask_v, mask_w, mask_default,
        )
    else:
        _found, masks, *_ = source_surface.sample(
            points, empty_i, empty_i, empty_f, mask_v, mask_w, mask_default,
        )
        _found, _masks, rows, bones, weights = weight_surface.sample(
            points, src_v, src_b, src_w, empty_i, empty_f, 0.0,
        )

    per_row = {}
    for r, b, w in zip(rows, bones, weights):
        per_row.setdefault(r, {})[names[b]] = w

    weight_map = {}
    mask_map = {}
    for row, v_idx in enumerate(verts):
        if masks[row] <= 0.0:
            continue
        mask_map[v_idx] = masks[row]
        if row in per_row:
            weight_map[v_idx] = per_row[row]
    return weight_map, mask_map


def compute_layer_payloads(
    layer_output, transfer_method, target, source_surface,
    composite_weights, composite_mask, layers, merge_name,
    allowed_source_verts=None, allowed_target_verts=None, weight_surface=None,
    target_positions=None,
):
    if layer_output == 'MERGE':
        if transfer_method == 'VERTEX_ID':
            weight_map = {
                v_idx: bw for v_idx, bw in composite_weights.items()
                if (allowed_source_verts is None or v_idx in allowed_source_verts)
                and (allowed_target_verts is None or v_idx in allowed_target_verts)
            }
            mask_map = {v_idx: 1.0 for v_idx in weight_map}
        else:
            weight_map, mask_map = closest_surface_point_transfer(
                target, source_surface, composite_weights, composite_mask, 0.0,
                allowed_target_verts=allowed_target_verts, weight_surface=weight_surface,
                target_positions=target_positions,
            )
        return [(merge_name, weight_map, mask_map)]

    layer_payloads = []
    for name, layer_weights, layer_mask, mask_default in layers:
        if transfer_method == 'VERTEX_ID':
            layer_weight_map = {
                v_idx: bw for v_idx, bw in layer_weights.items() if bw
                and (allowed_source_verts is None or v_idx in allowed_source_verts)
                and (allowed_target_verts is None or v_idx in allowed_target_verts)
            }
            layer_mask_map = {v_idx: 1.0 for v_idx in layer_weight_map}
        else:
            layer_weight_map, layer_mask_map = closest_surface_point_transfer(
                target, source_surface, layer_weights, layer_mask, mask_default,
                allowed_target_verts=allowed_target_verts, weight_surface=weight_surface,
                target_positions=target_positions,
            )
        layer_payloads.append((name, layer_weight_map, layer_mask_map))
    return layer_payloads


def write_layers_to_target(context, target, insert_method, layer_payloads):
    facade = CoreFacade(context)

    if not _has_layer_system(target):
        _run_in_object_context(context, facade.init_layer_system)
        CoreFacade.debug_log("feature_domains", f"weight_transfer: init_layer_system() on {target.name!r}")

    old_slots_to_remove = []
    existing_names = set()
    if insert_method == 'REPLACE':
        old_slots_to_remove = sorted(
            (m.get("index", -1) for m in facade.get_meta_list() if m.get("index", -1) >= 0),
            reverse=True,
        )
    else:
        existing_names = {m.get("name") for m in facade.get_meta_list() if m.get("name")}

    def _do_write():
        last_slot = -1
        for name, weight_dict, mask_dict in reversed(layer_payloads):
            unique_name = unique_layer_name(existing_names, name)
            existing_names.add(unique_name)

            new_slot = facade.create_layer(unique_name)
            CoreFacade.debug_log(
                "feature_domains",
                f"weight_transfer: create_layer(name={unique_name!r}) on {target.name!r} -> slot={new_slot} verts={len(weight_dict)}",
            )
            if new_slot is None or new_slot < 0:
                continue
            last_slot = new_slot
            facade.switch_to_layer(new_slot)
            facade.write_layer_dict(weight_dict)
            if mask_dict:
                facade.write_mask_dict(mask_dict)

        if old_slots_to_remove:
            for slot in old_slots_to_remove:
                facade.remove_layer(slot)
            CoreFacade.debug_log(
                "feature_domains",
                f"weight_transfer: removed {len(old_slots_to_remove)} pre-existing layer(s) on {target.name!r} after creating replacements",
            )
            return facade.get_active_layer_index()
        return last_slot

    last_slot = _run_in_object_context(context, _do_write)

    facade.finish()

    if last_slot >= 0:
        _select_only_layer(target, last_slot)
    sync_layers_to_ui_collection(target)
    _enforce_visualizer_from_tab_state(context)
