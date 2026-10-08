
import bmesh
import numpy as np

from ...weight_apply.public_api import write_vertex_select


def _exceeding_verts(layer_data: dict, max_influences: int) -> set:
    return {v_idx for v_idx, bones in layer_data.items() if len(bones) > max_influences}


def find_exceeded(core_facade, max_influences: int) -> set:
    layer_data = core_facade.read_active_layer()
    return _exceeding_verts(layer_data, max_influences)


def _clamp_vertex(bones: dict, max_influences: int) -> dict:
    total = sum(bones.values())
    top = sorted(bones.items(), key=lambda item: item[1], reverse=True)[:max_influences]
    kept_total = sum(w for _, w in top)
    if kept_total <= 1e-9:
        return dict(top)
    scale = total / kept_total
    return {bone: w * scale for bone, w in top}


def limit_total(core_facade, max_influences: int) -> int:
    selected = set(core_facade.get_selected_verts())
    with core_facade.mutate_active_layer() as layer_data:
        targets = selected if selected else set(layer_data.keys())
        changed = 0
        for v_idx in targets:
            bones = layer_data.get(v_idx)
            if not bones or len(bones) <= max_influences:
                continue
            layer_data[v_idx] = _clamp_vertex(bones, max_influences)
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
