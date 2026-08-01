"""Layer merge algorithms -- pure data operations, no bpy dependency.

Provides merge_selected() as the public entry point, backed by merge_layers()
and _composite_subset_coverage() for the composite-subset logic.
"""

from __future__ import annotations

from .codec import decode_layer_dict, mask_value, _composite_layers


def merge_layers(meta_list, layer_data_map, mask_data_map, selected_indices, num_verts):
    """Composite ONLY the layers in *selected_indices*, ignoring all others.

    Used by merge_selected_layers: the caller writes the returned weight dict
    into the target layer's slot, the mask dict into the target mask slot, and
    deletes the other selected layers.

    Returns:
        ``(weight_dict, mask_dict)`` tuple:
        - weight_dict: ``{v_idx: {bone_name: weight}}``
        - mask_dict:  ``{v_idx: float}`` (omits entries <= 0.001)
    """
    selected_set = set(selected_indices)
    subset_meta = [
        {**layer, "visible": True}
        for layer in meta_list
        if layer.get("index") in selected_set
    ]
    weight_dict = _composite_layers(
        subset_meta, layer_data_map, mask_data_map, {}, num_verts
    )

    # Find the foundation layer of the subset (bottom of the selected stack).
    base_idx = None
    for layer in reversed(subset_meta):
        l_idx = int(layer["index"])
        raw = layer_data_map.get(l_idx)
        if raw and decode_layer_dict(raw):
            base_idx = l_idx
            break

    # Backfill vertices where the compositor produced no output because the
    # foundation layer's own mask was at/near zero (common for newly created
    # layers with mask_default: 0.0). This only touches vertices the
    # compositor left completely void; any vertex that received a real
    # contribution is left exactly as _composite_layers computed it.
    if base_idx is not None:
        base_weights = {
            int(v): w
            for v, w in decode_layer_dict(layer_data_map.get(base_idx)).items()
        }
        for v_idx, bone_weights in base_weights.items():
            if v_idx in weight_dict or not bone_weights:
                continue
            total = sum(bone_weights.values())
            if total > 0.001:
                weight_dict[v_idx] = {
                    name: w / total for name, w in bone_weights.items() if w > 0.001
                }

    mask_dict = _composite_subset_coverage(
        meta_list, mask_data_map, selected_set, num_verts
    )
    return weight_dict, mask_dict


def _composite_subset_coverage(meta_list, mask_data_map, selected_indices, num_verts):
    """Alpha-over composite of the MASK channel across *selected_indices*.

    Processes in meta_list stack order (bottom-to-top). Returns
    ``{v_idx: float}``, omitting entries with coverage <= 0.001.

    Standard "over" formula:
        new_coverage = m + coverage * (1 - m)
    where m is each layer's per-vertex mask value.
    """
    ordered = [layer for layer in reversed(meta_list)
               if layer.get("index") in selected_indices]
    coverage = [0.0] * num_verts
    for layer in ordered:
        l_idx = layer["index"]
        mask_default = float(layer.get("mask_default", 1.0))
        raw_mask = mask_data_map.get(l_idx)
        mask_dict = (
            {int(v): w for v, w in decode_layer_dict(raw_mask).items()}
            if raw_mask else {}
        )
        for v in range(num_verts):
            m = mask_value(mask_dict.get(v, mask_default))
            m = max(0.0, min(1.0, m))
            coverage[v] = coverage[v] * (1.0 - m) + m
    return {v: c for v, c in enumerate(coverage) if c > 0.001}


def merge_selected(
    meta_list: list,
    layer_data_map: dict,
    mask_data_map: dict,
    selected_indices: list[int],
    target_index: int,
    num_verts: int,
) -> tuple[dict, dict, list] | None:
    """Composite selected layers and return merged data + updated metadata.

    Validates preconditions, delegates compositing to ``merge_layers()``, and
    removes non-target selected entries from *meta_list*.

    Args:
        meta_list: Full layer metadata stack in display order.
        layer_data_map: ``{layer_index: raw_encoded_str}`` for every layer.
        mask_data_map: ``{layer_index: raw_encoded_str}`` for masked layers.
        selected_indices: Layer slot indices to merge. Must include *target_index*.
        target_index: The slot that will receive the merged result.
        num_verts: Vertex count of the target mesh.

    Returns:
        ``None`` when preconditions fail (*target_index* not in *selected_indices*
        or fewer than two indices given). Otherwise
        ``(weight_dict, mask_dict, new_meta_list)``.
    """
    if target_index not in selected_indices or len(selected_indices) < 2:
        return None

    weight_dict, mask_dict = merge_layers(
        meta_list, layer_data_map, mask_data_map, set(selected_indices), num_verts
    )

    others = sorted((i for i in selected_indices if i != target_index), reverse=True)
    new_meta = list(meta_list)
    for idx in others:
        new_meta = [entry for entry in new_meta if entry.get("index") != idx]

    return weight_dict, mask_dict, new_meta
