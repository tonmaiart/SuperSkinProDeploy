

def map_layer_to_int(raw_layer_dict: dict, bone_to_id: dict) -> dict:
    return {
        int(v_idx): {
            int(bone_to_id[b_name]): float(w)
            for b_name, w in weights.items()
            if b_name in bone_to_id
        }
        for v_idx, weights in raw_layer_dict.items()
    }


def map_layer_to_string(calc_layer_dict: dict, id_to_bone: dict) -> dict:
    return {
        int(v_idx): {
            str(id_to_bone[b_id]): float(w)
            for b_id, w in weights.items()
            if b_id in id_to_bone
        }
        for v_idx, weights in calc_layer_dict.items()
    }


def _prune_zero_bones(layer_str: dict) -> None:
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
