
import numpy as np

from .. import mesh_arrays


def _run(core_facade, fn_name, *args):
    mesh = core_facade.get_mesh()
    return core_facade.get_rust_gateway("hammer").call(
        fn_name, len(mesh.vertices), mesh_arrays.edge_verts(mesh), *args,
    )


def hammer_weights(core_facade, selected, layer_data) -> dict:
    names, src_v, src_b, src_w = mesh_arrays.weights_coo(layer_data)
    rows, bones, weights, resolved = _run(
        core_facade, "rust_hammer_weights",
        np.fromiter(selected, dtype=np.int64, count=len(selected)), src_v, src_b, src_w,
    )
    per_vertex = mesh_arrays.coo_rows(rows, bones, weights, names)
    return {v: per_vertex.get(v, {}) for v in resolved}


def hammer_mask(core_facade, selected, mask_data, mask_default) -> dict:
    mask_v, mask_w = mesh_arrays.mask_arrays(mask_data)
    verts, values = _run(
        core_facade, "rust_hammer_mask",
        np.fromiter(selected, dtype=np.int64, count=len(selected)), mask_v, mask_w, mask_default,
    )
    return dict(zip(verts, values))


_pending_blend = 1.0


def set_blend(value: float) -> None:
    global _pending_blend
    _pending_blend = max(0.0, min(1.0, float(value)))


def _lerp_weights(old, new, t):
    out = {}
    for bone in old.keys() | new.keys():
        w = old.get(bone, 0.0) * (1.0 - t) + new.get(bone, 0.0) * t
        if w > 1e-6:
            out[bone] = w
    return out


def _active_mask_default(core_facade) -> float:
    active_idx = core_facade.get_active_layer_index()
    for m in core_facade.get_meta_list():
        if m.get("index", -1) == active_idx:
            return float(m.get("mask_default", 1.0))
    return 1.0


def hammer(core_facade) -> int:
    selected = set(core_facade.get_selected_verts())
    if not selected:
        raise ValueError("No vertices selected -- select some before clicking Hammer")

    t = _pending_blend

    if core_facade.is_mask_context():
        mask_default = _active_mask_default(core_facade)
        mask_data = core_facade.get_active_mask_dict()
        result = hammer_mask(core_facade, selected, mask_data, mask_default)
        if not result:
            raise ValueError("Selected vertices have no unselected neighbours to hammer from")
        for v_idx, new_val in result.items():
            if t < 1.0:
                old_val = mask_data.get(v_idx, mask_default)
                new_val = old_val * (1.0 - t) + new_val * t
            mask_data[v_idx] = new_val
        core_facade.write_mask_dict(mask_data)
        core_facade.finish(color_only=False)
        return len(result)

    with core_facade.mutate_active_layer() as layer_data:
        result = hammer_weights(core_facade, selected, layer_data)
        if not result:
            raise ValueError("Selected vertices have no unselected neighbours to hammer from")
        for v_idx, bone_weights in result.items():
            if t < 1.0:
                bone_weights = _lerp_weights(layer_data.get(v_idx, {}), bone_weights, t)
            if bone_weights:
                layer_data[v_idx] = bone_weights
            else:
                layer_data.pop(v_idx, None)

    return len(result)
