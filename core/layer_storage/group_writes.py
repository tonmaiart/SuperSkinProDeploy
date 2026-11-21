
import numpy as np

from . import group_arrays


def surface_rows_coo(coo: tuple, id_to_bone: dict, surface: frozenset) -> tuple:
    vert_ids, bone_ids, weights = coo
    ids = [b for b, name in id_to_bone.items() if name in surface and b >= 0]
    top = max([int(np.max(bone_ids, initial=-1))] + ids) + 1
    on = np.zeros(max(top, 0), dtype=bool)
    on[ids] = True
    bone_ids = np.asarray(bone_ids, dtype=np.int64)
    keep = (bone_ids >= 0) & on[np.maximum(bone_ids, 0)] & (np.asarray(weights) > 0.0)
    return (np.asarray(vert_ids, dtype=np.int64)[keep], bone_ids[keep],
            np.asarray(weights, dtype=np.float64)[keep])


def surface_rows_int(layer_int: dict, id_to_bone: dict, dirty_verts, surface: frozenset) -> dict:
    ids = {b for b, name in id_to_bone.items() if name in surface}
    return {v: {b: float(w) for b, w in layer_int.get(v, {}).items() if b in ids and w > 0.0}
            for v in dirty_verts}


def targets_coo(targets: dict) -> tuple:
    from ...core_subsystems.rust_weight_engine.flat_array_bridge import int_layer_columns
    return int_layer_columns(targets)


def _read_groups(elems) -> tuple:
    n = len(elems)
    groups, weights = [0] * n, [0.0] * n
    if n:
        elems.foreach_get("group", groups)
        elems.foreach_get("weight", weights)
    return groups, weights


class GroupWriteState:
    """Per group, the float32 weight a stroke left on each vertex (-1 for no entry, NaN if unread);
    valid while nothing else writes them."""

    def __init__(self, count: int):
        self.known = np.zeros(count, dtype=bool)
        self.columns: dict = {}

    def column(self, group: int):
        col = self.columns.get(group)
        if col is None:
            col = np.full(len(self.known), np.nan, dtype=np.float32)
            col[self.known] = -1.0
            self.columns[group] = col
        return col

    def nbytes(self) -> int:
        return self.known.nbytes + sum(col.nbytes for col in self.columns.values())

    def read(self, vertices, verts) -> None:
        new = verts[~self.known[verts]]
        if not len(new):
            return
        vert_ids, groups, weights = [], [], []
        for v in new.tolist():
            g, w = _read_groups(vertices[v].groups)
            vert_ids.extend([v] * len(g))
            groups.extend(g)
            weights.extend(w)
        for col in self.columns.values():
            col[new] = -1.0
        self.known[new] = True
        if groups:
            vert_ids = np.array(vert_ids, dtype=np.int64)
            groups = np.array(groups, dtype=np.int64)
            weights = np.array(weights, dtype=np.float32)
            for g in np.unique(groups).tolist():
                sel = groups == g
                self.column(g)[vert_ids[sel]] = weights[sel]


def write_group_entries(vgs, mesh, verts, passes, state: GroupWriteState) -> None:
    verts = np.asarray(verts, dtype=np.int64)
    if not len(verts):
        return
    state.read(mesh.vertices, verts)
    owned = set()
    for pass_owned, *_rows in passes:
        owned.update(pass_owned)
    tv = np.concatenate([np.asarray(p[1], dtype=np.int64) for p in passes])
    tg = np.concatenate([np.asarray(p[2], dtype=np.int64) for p in passes])
    tw = np.clip(np.concatenate([np.asarray(p[3], dtype=np.float64) for p in passes]),
                 0.0, 1.0).astype(np.float32)
    pos = np.minimum(np.searchsorted(verts, tv), len(verts) - 1)
    inside = verts[pos] == tv
    if not inside.all():
        tg, tw, pos = tg[inside], tw[inside], pos[inside]

    wrote = False
    moved = []
    groups = {g for g in state.columns if g in owned}
    groups.update(np.unique(tg).tolist())
    for g in sorted(groups):
        col = state.column(g)
        before = col[verts]
        after = np.full(len(verts), -1.0, dtype=np.float32)
        sel = tg == g
        after[pos[sel]] = tw[sel]
        diff = before != after
        if not diff.any():
            continue
        left = diff & (after < 0.0)
        if left.any():
            vgs[g].remove(verts[left].tolist())
        joined = diff & (before < 0.0) & (after >= 0.0)
        if joined.any():
            group_arrays.add_members(vgs[g], verts[joined], after[joined])
        changed = diff & (before >= 0.0) & (after >= 0.0)
        if changed.any():
            moved.append((verts[changed], np.full(int(changed.sum()), g), after[changed]))
        col[verts[diff]] = after[diff]
        wrote = True
    if moved:
        _set_weights(mesh, vgs, *(np.concatenate(parts) for parts in zip(*moved)))
    if wrote:
        mesh.update_tag()


def _set_weights(mesh, vgs, verts, groups, weights) -> None:
    unique, counts = np.unique(verts, return_counts=True)
    if len(verts) < 2 * len(unique):
        for g in np.unique(groups).tolist():
            sel = groups == g
            group_arrays.add_members(vgs[g], verts[sel], weights[sel])
        return
    order = np.lexsort((groups, verts))
    verts, groups, weights = verts[order].tolist(), groups[order].tolist(), weights[order].tolist()
    vertices = mesh.vertices
    start = 0
    for count in counts.tolist():
        end = start + count
        elems = vertices[verts[start]].groups
        n = len(elems)
        have_groups, have_weights = [0] * n, [0.0] * n
        elems.foreach_get("group", have_groups)
        elems.foreach_get("weight", have_weights)
        new = dict(zip(groups[start:end], weights[start:end]))
        elems.foreach_set("weight", [new.get(g, w) for g, w in zip(have_groups, have_weights)])
        start = end
