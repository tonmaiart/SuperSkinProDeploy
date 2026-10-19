from __future__ import annotations
from ....core.facade import CoreFacade


def build_name_pairs(bone_names, search: str, replace: str) -> dict[str, str]:
    if not search:
        return {}
    names = set(bone_names)
    pairs = {}
    for name in sorted(names):
        if search not in name:
            continue
        target = name.replace(search, replace)
        if target != name and target in names:
            pairs[name] = target
    return pairs


def move_weight_by_name(ctrl: CoreFacade, search: str, replace: str, factor: float) -> tuple[int, int]:
    if ctrl.is_mask_context():
        raise ValueError("Move Weight by Name is not available while editing a mask")
    if not search:
        raise ValueError("Search text is empty")

    factor = min(1.0, max(0.0, float(factor)))
    locks = ctrl.get_bone_locks()
    changed_verts = set()

    with ctrl.mutate_active_layer(color_only=False) as layer_data:
        bone_to_id, _id_to_bone = ctrl.get_unified_mapping()
        deform_ids = ctrl.get_deform_bone_ids()
        bone_names = [name for name, bid in bone_to_id.items() if bid in deform_ids]
        pairs = {
            src: tgt for src, tgt in build_name_pairs(bone_names, search, replace).items()
            if not locks.get(src, False) and not locks.get(tgt, False)
        }
        if not pairs:
            raise ValueError(f"No unlocked bone pairs match '{search}' -> '{replace}'")

        selected = ctrl.get_selected_verts()
        target_verts = selected if len(selected) > 0 else list(layer_data.keys())

        for v_idx in target_verts:
            weights = layer_data.get(v_idx)
            if not weights:
                continue
            for src, tgt in pairs.items():
                src_w = weights.get(src, 0.0)
                if src_w <= 0.0:
                    continue
                total = src_w + weights.get(tgt, 0.0)
                new_tgt = total * factor
                weights[tgt] = new_tgt
                weights[src] = total - new_tgt
                changed_verts.add(v_idx)

        if not changed_verts:
            raise ValueError("No weight to move on the source bones")

    return len(pairs), len(changed_verts)
