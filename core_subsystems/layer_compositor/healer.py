
from .codec import mask_value


def interpolate_weight_from_neighbours(v_idx, neighbours, layer_dict):
    valid = [n for n in neighbours.get(v_idx, ()) if n in layer_dict]
    if not valid:
        return {}

    all_vg = set()
    for n in valid:
        all_vg.update(layer_dict[n].keys())

    result = {}
    for vg_idx in all_vg:
        total = 0.0
        count = 0
        for n in valid:
            total += layer_dict[n].get(vg_idx, 0.0)
            count += 1
        result[vg_idx] = total / count
    return result


def interpolate_mask_from_neighbours(v_idx, neighbours, mask_dict):
    valid = [n for n in neighbours.get(v_idx, ()) if n in mask_dict]
    if not valid:
        return None

    total = 0.0
    for n in valid:
        total += mask_value(mask_dict[n])
    return total / len(valid)


def heal_layer_dict(layer_dict, neighbours, num_verts):
    if not layer_dict:
        return layer_dict, False

    modified = False

    stale = [v for v in layer_dict if v >= num_verts]
    for v in stale:
        del layer_dict[v]
    if stale:
        modified = True

    missing = [v for v in range(num_verts) if v not in layer_dict]
    if missing:
        for v_idx in missing:
            layer_dict[v_idx] = interpolate_weight_from_neighbours(
                v_idx, neighbours, layer_dict
            )
        for _ in range(2):
            retry = [v for v in range(num_verts)
                     if v in layer_dict and not layer_dict[v]]
            if not retry:
                break
            for v_idx in retry:
                interp = interpolate_weight_from_neighbours(
                    v_idx, neighbours, layer_dict
                )
                if interp:
                    layer_dict[v_idx] = interp
        modified = True

    return layer_dict, modified


def heal_mask_dict(mask_dict, neighbours, num_verts, mask_default):
    if not mask_dict:
        return mask_dict, False

    modified = False

    stale = [v for v in mask_dict if v >= num_verts]
    for v in stale:
        del mask_dict[v]
    if stale:
        modified = True

    missing = [v for v in range(num_verts) if v not in mask_dict]
    if missing:
        for v_idx in missing:
            interp = interpolate_mask_from_neighbours(
                v_idx, neighbours, mask_dict
            )
            mask_dict[v_idx] = interp if interp is not None else mask_default
        modified = True

    return mask_dict, modified
