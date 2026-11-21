
from dataclasses import dataclass

import numpy as np

from .. import mesh_arrays

WEIGHT = "WEIGHT"
MASK = "MASK"


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


def hammer_weights(core_facade, selected, layer_data, weld=None) -> dict:
    rep, groups = weld if weld is not None else _weld_groups(core_facade)
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


def hammer_mask(core_facade, selected, mask_data, mask_default, weld=None) -> dict:
    rep, groups = weld if weld is not None else _weld_groups(core_facade)
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


_pending_target = None


def set_target(layer_index, channel=None) -> None:
    global _pending_target
    _pending_target = None if layer_index is None else (int(layer_index), channel)


def _meta_entry(core_facade, layer_index):
    for m in core_facade.get_meta_list():
        if m.get("index", -1) == layer_index:
            return m
    return {}


def _mask_default(entry) -> float:
    return float(entry.get("mask_default", 1.0))


def _selection(core_facade) -> set:
    selected = set(core_facade.get_selected_verts())
    if not selected:
        raise ValueError("No vertices selected -- select some before clicking Hammer")
    return selected


@dataclass(frozen=True)
class Candidate:
    layer_index: int
    channel: str
    name: str
    score: float

    @property
    def key(self) -> str:
        return f"{self.layer_index}:{self.channel}"


def _skin_matrices(obj):
    arm = obj.find_armature()
    if arm is None or arm.data.pose_position == 'REST':
        return None
    to_obj = obj.matrix_world.inverted() @ arm.matrix_world
    from_obj = arm.matrix_world.inverted() @ obj.matrix_world
    mats = {pb.name: np.array(to_obj @ pb.matrix @ pb.bone.matrix_local.inverted() @ from_obj)
            for pb in arm.pose.bones}
    if all(np.allclose(m, np.eye(4), atol=1e-6) for m in mats.values()):
        return None
    return mats


def _skinned(rows, coords, mats) -> dict:
    out = {}
    for v, row in rows.items():
        co = coords[v]
        p, total = np.zeros(4), 0.0
        for bone, w in row.items():
            m = mats.get(bone)
            if m is not None:
                p += w * (m @ co)
                total += w
        out[v] = p[:3] / total if total > 1e-4 else co[:3]
    return out


def _weight_change(before, after) -> float:
    return sum(abs(after[v].get(b, 0.0) - row.get(b, 0.0))
               for v, row in before.items() for b in row.keys() | after[v].keys())


def rank_targets(core_facade) -> list:
    selected = sorted(_selection(core_facade))
    weld = _weld_groups(core_facade)
    _bone_to_id, id_to_bone = core_facade.get_unified_mapping()
    before = core_facade.composite_preview(selected)
    obj = core_facade.obj
    mats = _skin_matrices(obj)
    coords = None
    if mats is not None:
        coords = {v: np.array((*obj.data.vertices[v].co, 1.0)) for v in selected}
        posed_before = _skinned(before, coords, mats)
    found = []
    for entry in core_facade.get_meta_list():
        if not entry.get("visible", True):
            continue
        idx = int(entry.get("index", -1))
        if idx < 0:
            continue
        name = str(entry.get("name", f"Layer {idx}"))
        trials = []
        if not entry.get("is_group"):
            layer_int = core_facade.read_layer_int(idx)
            if layer_int:
                new = hammer_weights(core_facade, selected, layer_int, weld)
                rows = {v: {id_to_bone.get(b, str(b)): w for b, w in new.get(v, layer_int.get(v, {})).items()}
                        for v in selected}
                trials.append((WEIGHT, {"layer_overrides": {idx: rows}}))
        default = _mask_default(entry)
        own = core_facade.get_layer_mask_dict(idx)
        mask = dict(own)
        mask.update(hammer_mask(core_facade, selected, own, default, weld))
        trials.append((MASK, {"mask_overrides": {idx: mask}}))
        for channel, override in trials:
            after = core_facade.composite_preview(selected, **override)
            change = _weight_change(before, after)
            if change <= 1e-6:
                continue
            moved = 0.0
            if mats is not None:
                posed_after = _skinned(after, coords, mats)
                moved = sum(float(np.sum((posed_after[v] - posed_before[v]) ** 2)) for v in selected)
            found.append((idx, channel, name, moved, change))
    use_moved = any(f[3] > 1e-12 for f in found)
    out = [Candidate(idx, ch, name, moved if use_moved else change) for idx, ch, name, moved, change in found]
    out.sort(key=lambda c: c.score, reverse=True)
    return out


def _hammer_layer_weights(core_facade, idx, selected) -> int:
    from ...weight_apply.public_api import mirror_scope
    layer_int = core_facade.read_layer_int(idx)
    _bone_to_id, id_to_bone = core_facade.get_unified_mapping()
    scope = mirror_scope(core_facade, selected)
    result = hammer_weights(core_facade, scope.verts, layer_int)
    if not result:
        raise ValueError("Selected vertices have no unselected neighbours to hammer from")
    rows = dict(result)
    scope.split_seam(rows, core_facade.get_locks_by_id(idx))
    for v_idx, bone_weights in rows.items():
        if bone_weights:
            layer_int[v_idx] = bone_weights
        else:
            layer_int.pop(v_idx, None)
    core_facade.write_layer_int(idx, layer_int, id_to_bone)
    core_facade.finish()
    return len(result)


def _hammer_layer_mask(core_facade, idx, entry, selected) -> int:
    default = _mask_default(entry)
    mask_data = core_facade.get_layer_mask_dict(idx)
    from ...weight_apply.public_api import mirror_scope
    result = hammer_mask(core_facade, mirror_scope(core_facade, selected, is_mask=True).verts, mask_data, default)
    if not result:
        raise ValueError("Selected vertices have no unselected neighbours to hammer from")
    mask_data.update(result)
    core_facade.write_layer_mask_dict(idx, mask_data)
    core_facade.finish(color_only=False)
    return len(result)


def hammer(core_facade) -> int:
    selected = _selection(core_facade)
    if _pending_target is None:
        idx = core_facade.get_active_layer_index()
        channel = MASK if core_facade.is_mask_context() else WEIGHT
    else:
        idx, channel = _pending_target
    entry = _meta_entry(core_facade, idx)
    if not entry:
        raise ValueError("The layer to hammer no longer exists")
    if channel == MASK or entry.get("is_group"):
        return _hammer_layer_mask(core_facade, idx, entry, selected)
    return _hammer_layer_weights(core_facade, idx, selected)
