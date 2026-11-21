
from ...core_subsystems.context_selection_service import ContextSelectionService as _CSS


def normalize_weights_in_storage(ctrl, vertex_index, active_vg_name, layer_dict):
    managed = ctrl.storage.managed_vg_names(ctrl.obj)
    return _CSS.normalize_weights(
        layer_dict=layer_dict,
        vertex_index=vertex_index,
        active_vg_name=active_vg_name,
        vg_names=[vg.name for vg in ctrl.obj.vertex_groups if vg.name in managed],
        bone_locks=ctrl.get_bone_locks(),
        is_mask=ctrl.is_mask_context(),
    )
