"""Pure topology-aware auto-healing for layer and mask storage.

After mesh topology changes (loop cuts, extrude, subdivide, etc.) new
vertices appear that have no entries in the stored layer/mask dicts.
These functions interpolate values from edge-connected neighbours and
prune entries for deleted vertices.

No bpy -- operates on plain ``dict`` structures.
"""

from .codec import mask_value


def interpolate_weight_from_neighbours(v_idx, neighbours, layer_dict):
    """Average per-vg weights from edge-connected neighbours.

    Args:
        v_idx: Index of the vertex to heal.
        neighbours: ``{v_idx: (neighbour_v_idx, ...)}`` edge map.
        layer_dict: ``{v_idx: {vg_idx: weight}}`` -- current layer data.

    Returns:
        ``{vg_idx: float}`` -- empty dict when no valid neighbour has data.
    """
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
    """Average mask-float from edge-connected neighbours.

    Returns ``float`` or ``None`` when no valid neighbour has data.
    """
    valid = [n for n in neighbours.get(v_idx, ()) if n in mask_dict]
    if not valid:
        return None

    total = 0.0
    for n in valid:
        total += mask_value(mask_dict[n])
    return total / len(valid)


def heal_layer_dict(layer_dict, neighbours, num_verts):
    """Prune stale entries and interpolate missing vertices in a layer dict.

    Args:
        layer_dict: ``{v_idx: {vg_idx: weight}}`` -- mutable, modified in-place.
        neighbours: ``{v_idx: (neighbour_v_idx, ...)}`` edge map.
        num_verts: Current vertex count.

    Returns:
        ``(layer_dict, modified)`` -- *layer_dict* is the same object,
        *modified* is ``True`` when any entry was added or removed.
    """
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
        # Second and third pass: chain-interpolate vertices whose neighbours
        # were just filled by the previous pass.
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
    """Prune stale entries and interpolate missing vertices in a mask dict.

    Args:
        mask_dict: ``{v_idx: raw_mask_value}`` -- mutable, modified in-place.
        neighbours: ``{v_idx: (neighbour_v_idx, ...)}`` edge map.
        num_verts: Current vertex count.
        mask_default: Fallback value for vertices where no neighbour has data.

    Returns:
        ``(mask_dict, modified)``.
    """
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
