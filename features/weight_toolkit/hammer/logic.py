
import numpy as np

from .. import mesh_arrays


def _run(core_facade, fn_name, *args, rep=None):
    mesh = core_facade.get_mesh()
    edges = mesh_arrays.edge_verts(mesh)
    if rep is not None:
        pairs = rep[edges.reshape(-1, 2)]
        edges = pairs[pairs[:, 0] != pairs[:, 1]].reshape(-1)
    return core_facade.get_rust_gateway("hammer").call(fn_name, len(mesh.vertices), edges, *args)


def _weld_groups(core_facade):
    from ...weight_apply.public_api import seam_weld_reps
    rep = seam_weld_reps(core_facade.get_mesh())
    if rep is None:
        return None, {}
    groups = {}
    for v in np.flatnonzero(rep != np.arange(len(rep))).tolist():
        groups.setdefault(int(rep[v]), [int(rep[v])]).append(v)
    return rep, groups


def _merged_selection(rep, selected):
    return {int(rep[v]) for v in selected} if rep is not None else selected


def _spread(result, groups):
    out = dict(result)
    for r, value in result.items():
        for v in groups.get(r, ()):
            out[v] = value
    return out


def hammer_weights(core_facade, selected, layer_data) -> dict:
    rep, groups = _weld_groups(core_facade)
    selected = _merged_selection(rep, selected)
    if groups:
        layer_data = dict(layer_data)
        for r, members in groups.items():
            layer_data[r] = _mean_rows([layer_data.get(v, {}) for v in members])
    names, src_v, src_b, src_w = mesh_arrays.weights_coo(layer_data)
    rows, bones, weights, resolved = _run(
        core_facade, "rust_hammer_weights",
        np.fromiter(selected, dtype=np.int64, count=len(selected)), src_v, src_b, src_w, rep=rep,
    )
    per_vertex = mesh_arrays.coo_rows(rows, bones, weights, names)
    return _spread({v: per_vertex.get(v, {}) for v in resolved}, groups)


def _mean_rows(rows):
    out = {}
    for row in rows:
        for bone, w in row.items():
            out[bone] = out.get(bone, 0.0) + w / len(rows)
    return out


def hammer_mask(core_facade, selected, mask_data, mask_default) -> dict:
    rep, groups = _weld_groups(core_facade)
    selected = _merged_selection(rep, selected)
    if groups:
        mask_data = dict(mask_data)
        for r, members in groups.items():
            mask_data[r] = sum(mask_data.get(v, mask_default) for v in members) / len(members)
    mask_v, mask_w = mesh_arrays.mask_arrays(mask_data)
    verts, values = _run(
        core_facade, "rust_hammer_mask",
        np.fromiter(selected, dtype=np.int64, count=len(selected)), mask_v, mask_w, mask_default, rep=rep,
    )
    return _spread(dict(zip(verts, values)), groups)


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
