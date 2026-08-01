"""Pure conversion helpers for layer dict key transformations.

Key formats:
    String-keyed (storage format): {v_idx (int): {"BoneName": weight (float)}}
    Integer-keyed (FFI/Rust format): {v_idx (int): {vg_index (int): weight (float)}}
"""


def map_layer_to_int(raw_layer_dict: dict, bone_to_id: dict) -> dict:
    """Convert storage-layer string bone-name keys to integer group-index keys.

    Bones absent from bone_to_id (orphans unknown to the current VG roster)
    are silently dropped. Stash them before calling this function if they need
    to be re-merged after Rust computation.

    Args:
        raw_layer_dict: {v_idx: {"BoneName": weight, ...}, ...}
        bone_to_id:     {"BoneName": vg_index, ...}

    Returns:
        {v_idx: {vg_index: weight, ...}, ...}  (int -> int -> float)
    """
    return {
        int(v_idx): {
            int(bone_to_id[b_name]): float(w)
            for b_name, w in weights.items()
            if b_name in bone_to_id
        }
        for v_idx, weights in raw_layer_dict.items()
    }


def map_layer_to_string(calc_layer_dict: dict, id_to_bone: dict) -> dict:
    """Convert math-core integer group-index keys to storage-facing string keys.

    Integer IDs absent from id_to_bone are silently dropped. This handles
    synthetic orphan IDs that were mapped via get_unified_mapping() — they have
    entries in id_to_bone, so they are included.

    Args:
        calc_layer_dict: {v_idx: {vg_index: weight, ...}, ...}
        id_to_bone:      {vg_index: "BoneName", ...}

    Returns:
        {v_idx: {"BoneName": weight, ...}, ...}  (int -> str -> float)
    """
    return {
        int(v_idx): {
            str(id_to_bone[b_id]): float(w)
            for b_id, w in weights.items()
            if b_id in id_to_bone
        }
        for v_idx, weights in calc_layer_dict.items()
    }


def _prune_zero_bones(layer_str: dict) -> None:
    """Remove bone names with no influence (weight <= 0.0) across all vertices, in-place.

    A bone is considered zero if it has zero weight on every vertex in the layer.
    Vertices that become empty after pruning are also removed. Mutates the dict
    in-place and returns None.

    Args:
        layer_str: {v_idx: {"BoneName": weight, ...}, ...}  — modified in-place.
    """
    non_zero_bones: set = set()
    for weights in layer_str.values():
        for name, w in weights.items():
            if w > 0.0:
                non_zero_bones.add(name)
    for v_idx in list(layer_str):
        weights = layer_str[v_idx]
        for name in [n for n in weights if n not in non_zero_bones]:
            del weights[name]
        if not weights:
            del layer_str[v_idx]
