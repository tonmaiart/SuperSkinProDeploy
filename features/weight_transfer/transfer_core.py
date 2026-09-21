"""Shared "closest point on surface" weight-transfer engine."""

from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc

from ...core.facade import CoreFacade
from ...interface.utils.utils import (
    _has_layer_system,
    _run_in_object_context,
    _select_only_layer,
    _enforce_visualizer_from_tab_state,
    sync_layers_to_ui_collection,
)


def unique_layer_name(existing_names, name):
    """Disambiguate *name* against *existing_names* with a Blender-style '.001' suffix."""
    if name not in existing_names:
        return name
    i = 1
    while f"{name}.{i:03d}" in existing_names:
        i += 1
    return f"{name}.{i:03d}"


def ensure_armature_modifier(target, armature_obj):
    """Create/find target's Armature modifier and point it at *armature_obj*."""
    arm_mod = next((m for m in target.modifiers if m.type == 'ARMATURE'), None)
    if not arm_mod:
        arm_mod = target.modifiers.new(name="Armature", type='ARMATURE')
    arm_mod.object = armature_obj
    arm_mod.use_deform_preserve_volume = True
    return arm_mod


def ensure_native_vertex_groups(target, bone_names):
    """Create whichever of *bone_names* are missing from `target.vertex_groups`."""
    existing_vg_names = {vg.name for vg in target.vertex_groups}
    for name in bone_names:
        if name not in existing_vg_names:
            target.vertex_groups.new(name=name)


def build_surface(positions, triangles):
    """Build a world-space BVH from raw vertex positions + triangle index tuples."""
    bvh = BVHTree.FromPolygons(positions, triangles, all_triangles=True)
    return bvh, triangles, positions


def build_restricted_surface(positions, triangles, allowed_verts):
    """Build a BVH from only the subset of *triangles* whose 3 corners are all in *allowed_verts*."""
    restricted = [tri for tri in triangles if all(vi in allowed_verts for vi in tri)]
    if not restricted:
        return None
    return build_surface(positions, restricted)


def closest_surface_point_transfer(
    target, source_surface, layer_weights, layer_mask, mask_default,
    allowed_target_verts=None, weight_surface=None, target_positions=None,
):
    """True "closest point on surface" transfer (Maya's Closest Point / ngSkinTools' Transfer
    Weights): finds the."""
    mask_bvh, mask_tris, mask_world_verts = source_surface
    weight_bvh, weight_tris, weight_world_verts = (
        (mask_bvh, mask_tris, mask_world_verts) if weight_surface is None else weight_surface
    )
    matrix_world_tgt = target.matrix_world

    weight_map = {}
    mask_map = {}
    for v in target.data.vertices:
        if allowed_target_verts is not None and v.index not in allowed_target_verts:
            continue

        P = target_positions[v.index] if target_positions is not None else matrix_world_tgt @ v.co

        mask_value = 0.0
        location, _normal, tri_idx, _dist = mask_bvh.find_nearest(P)
        if tri_idx is not None:
            tri = mask_tris[tri_idx]
            bary = poly_3d_calc([mask_world_verts[i] for i in tri], location)
            for w, v_idx in zip(bary, tri):
                mask_value += w * layer_mask.get(v_idx, mask_default)

        bone_weights = {}
        w_location, _w_normal, w_tri_idx, _w_dist = weight_bvh.find_nearest(P)
        if w_tri_idx is not None:
            w_tri = weight_tris[w_tri_idx]
            w_bary = poly_3d_calc([weight_world_verts[i] for i in w_tri], w_location)
            for w, v_idx in zip(w_bary, w_tri):
                for bone, bw in layer_weights.get(v_idx, {}).items():
                    bone_weights[bone] = bone_weights.get(bone, 0.0) + w * bw

        if mask_value <= 0.0:
            continue

        mask_map[v.index] = mask_value
        if bone_weights:
            weight_map[v.index] = bone_weights

    return weight_map, mask_map


def compute_layer_payloads(
    layer_output, transfer_method, target, source_surface,
    composite_weights, composite_mask, layers, merge_name,
    allowed_source_verts=None, allowed_target_verts=None, weight_surface=None,
    target_positions=None,
):
    """Shared MERGE/SEPARATE x CLOSEST_DISTANCE/VERTEX_ID decision tree."""
    if layer_output == 'MERGE':
        if transfer_method == 'VERTEX_ID':
            # Vertex index == source index == target index for this method,
            # so the same index must pass both restrictions at once.
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
            # Source vertex index == target vertex index (checked by the caller),
            # so the layer's own dicts already ARE the target maps.
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
    """Create real SuperSkinPro Layer(s) on target and let finish() reflatten to native VGs."""
    facade = CoreFacade(context)
    ctrl = facade.get_ctrl()

    if not _has_layer_system(target):
        _run_in_object_context(context, ctrl.init_layer_system)
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

            new_slot = ctrl.create_layer(unique_name)
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
                ctrl.remove_layer(slot)
            CoreFacade.debug_log(
                "feature_domains",
                f"weight_transfer: removed {len(old_slots_to_remove)} pre-existing layer(s) on {target.name!r} after creating replacements",
            )
            return ctrl.active_layer_index
        return last_slot

    last_slot = _run_in_object_context(context, _do_write)

    facade.finish()

    if last_slot >= 0:
        _select_only_layer(target, last_slot)
    sync_layers_to_ui_collection(target)
    _enforce_visualizer_from_tab_state(context)
