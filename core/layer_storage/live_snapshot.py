
from .bmesh_io import iter_group_weights
from .geometry import get_local_mapping


def harvest_live_weights(obj, vg_indices=None) -> dict:
    idx_to_name = get_local_mapping(obj)[1]
    if vg_indices is not None:
        idx_to_name = {i: n for i, n in idx_to_name.items() if i in vg_indices}

    data = {}
    for v_idx, items in iter_group_weights(obj.data):
        vw = {idx_to_name[g]: w for g, w in items if w > 0.0 and g in idx_to_name}
        if vw:
            data[v_idx] = vw
    return data
