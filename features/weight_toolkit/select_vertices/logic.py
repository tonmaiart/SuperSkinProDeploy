from __future__ import annotations

import numpy as np

from ....core.facade import CoreFacade
from ...weight_apply.public_api import read_vertex_select, write_vertex_select

_MASK_EPSILON = 0.001


def _bone_affected_flags(ctrl: CoreFacade, mesh) -> np.ndarray:
    obj = ctrl.get_obj()
    names = set(ctrl.get_selected_bones_pool())
    active_id = ctrl.get_active_vg_id()
    if active_id is not None and active_id < len(obj.vertex_groups):
        names.add(obj.vertex_groups[active_id].name)
    if not names:
        raise ValueError("No bone selected")
    layer_dict = ctrl.read_active_layer()
    data_ops = CoreFacade.get_clipboard_data_ops()
    affected = set()
    for name in names:
        affected.update(data_ops.vertices_with_weight(layer_dict, name))
    flags = np.zeros(len(mesh.vertices), dtype=np.bool_)
    if affected:
        flags[np.fromiter(affected, dtype=np.int64)] = True
    return flags


def _mask_affected_flags(ctrl: CoreFacade, mesh) -> np.ndarray:
    active_idx = ctrl.get_active_layer_index()
    mask_default = 1.0
    for m in ctrl.get_meta_list():
        if m.get("index", -1) == active_idx:
            mask_default = float(m.get("mask_default", 1.0))
            break
    mask_dict = ctrl.get_active_mask_dict()
    return np.fromiter(
        (mask_dict.get(i, mask_default) > _MASK_EPSILON for i in range(len(mesh.vertices))),
        dtype=np.bool_, count=len(mesh.vertices),
    )


_pending_extend = False


def set_extend(value: bool) -> None:
    global _pending_extend
    _pending_extend = bool(value)


def select_affected(ctrl: CoreFacade, use_mask: bool, deselect: bool) -> int:
    global _pending_extend
    extend, _pending_extend = _pending_extend, False
    mesh = ctrl.get_mesh()
    flags = _mask_affected_flags(ctrl, mesh) if use_mask else _bone_affected_flags(ctrl, mesh)
    if extend and not deselect:
        selected = read_vertex_select(mesh)
        changed = flags & ~selected
        write_vertex_select(mesh, selected | flags)
    elif deselect:
        selected = read_vertex_select(mesh)
        changed = selected & flags
        write_vertex_select(mesh, selected & ~changed)
    else:
        changed = flags
        write_vertex_select(mesh, flags)
    mesh.update()
    return int(changed.sum())
