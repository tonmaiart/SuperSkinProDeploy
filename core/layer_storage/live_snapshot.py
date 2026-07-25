"""Harvest live Vertex Group weights into a plain layer dict.

Returns a *plain dict* (not an encoded blob) so that ``LayerStorageService``
can route it through the existing ``write_layer_dict`` → ``encode_layer_dict``
pipeline — one encoding code path instead of two.
"""


def harvest_live_weights(obj, vg_indices=None) -> dict:
    """Return ``{v_idx: {vg_name: weight}}`` from the object's real Vertex Groups.

    When *vg_indices* is provided, only groups whose index is in that set are
    included.
    """
    data = {}
    idx_to_name = {vg.index: vg.name for vg in obj.vertex_groups
                   if not vg.name.startswith("__ssp_")}

    for v in obj.data.vertices:
        vw = {}
        for g in v.groups:
            if vg_indices is not None and g.group not in vg_indices:
                continue
            if g.weight > 0.0 and g.group in idx_to_name:
                vw[idx_to_name[g.group]] = g.weight
        if vw:
            data[v.index] = vw

    return data
