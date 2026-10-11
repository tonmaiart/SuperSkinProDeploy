
import bmesh
import numpy as np

from ...weight_apply.public_api import write_vertex_select


def _deform_bone_names(core_facade) -> frozenset:
    return core_facade.managed_vg_names(core_facade.get_obj())


def _deform_count(bones: dict, deform: set) -> int:
    return sum(1 for bone in bones if bone in deform)


def _exceeding_verts(layer_data: dict, max_influences: int, deform: set) -> set:
    return {v_idx for v_idx, bones in layer_data.items()
            if _deform_count(bones, deform) > max_influences}


def find_exceeded(core_facade, max_influences: int) -> set:
    layer_data = core_facade.read_active_layer()
    return _exceeding_verts(layer_data, max_influences, _deform_bone_names(core_facade))


def _clamp_vertex(bones: dict, max_influences: int, deform: set) -> dict:
    deform_bones = {bone: w for bone, w in bones.items() if bone in deform}
    others = {bone: w for bone, w in bones.items() if bone not in deform}
    total = sum(deform_bones.values())
    top = sorted(deform_bones.items(), key=lambda item: item[1], reverse=True)[:max_influences]
    kept_total = sum(w for _, w in top)
    scale = total / kept_total if kept_total > 1e-9 else 1.0
    others.update({bone: w * scale for bone, w in top})
    return others


def limit_total(core_facade, max_influences: int) -> int:
    selected = set(core_facade.get_selected_verts())
    with core_facade.mutate_active_layer() as layer_data:
        deform = _deform_bone_names(core_facade)
        targets = selected if selected else set(layer_data.keys())
        changed = 0
        for v_idx in targets:
            bones = layer_data.get(v_idx)
            if not bones or _deform_count(bones, deform) <= max_influences:
                continue
            layer_data[v_idx] = _clamp_vertex(bones, max_influences, deform)
            changed += 1
    return changed


def _normalize_vertex(bones: dict, locks: dict, deform: set):
    locked_total = sum(w for bone, w in bones.items() if bone in deform and locks.get(bone))
    unlocked_total = sum(w for bone, w in bones.items() if bone in deform and not locks.get(bone))
    if unlocked_total <= 1e-9:
        return None
    scale = max(0.0, 1.0 - locked_total) / unlocked_total
    if abs(scale - 1.0) <= 1e-6:
        return None
    return {bone: (w * scale if bone in deform and not locks.get(bone) else w)
            for bone, w in bones.items()}


def normalize(core_facade) -> int:
    selected = set(core_facade.get_selected_verts())
    locks = core_facade.get_bone_locks()
    with core_facade.mutate_active_layer() as layer_data:
        deform = _deform_bone_names(core_facade)
        targets = selected if selected else set(layer_data.keys())
        changed = 0
        for v_idx in targets:
            bones = layer_data.get(v_idx)
            if not bones:
                continue
            normalized = _normalize_vertex(bones, locks, deform)
            if normalized is None:
                continue
            layer_data[v_idx] = normalized
            changed += 1
    return changed


def _write_selection(obj, mesh, target_indices: set) -> None:
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        for v in bm.verts:
            v.select = v.index in target_indices
        bm.select_flush_mode()
        bmesh.update_edit_mesh(mesh)
    else:
        flags = np.zeros(len(mesh.vertices), dtype=np.bool_)
        flags[list(target_indices)] = True
        write_vertex_select(mesh, flags)
        mesh.update()


def select_exceeded(core_facade, max_influences: int) -> int:
    exceeded = find_exceeded(core_facade, max_influences)
    _write_selection(core_facade.get_obj(), core_facade.get_mesh(), exceeded)
    return len(exceeded)
