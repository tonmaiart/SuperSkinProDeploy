"""Per-vertex normaliser for auto-assign.

Every function takes the UIController instance as its first parameter (``ctrl``).
Pure normalisation logic lives in core_subsystems/weight_ops.py; this module
is the bpy-context bridge that harvests data and delegates.

Mirror orchestration has been migrated to features/mirror/logic.py
(execute_mirror_pipeline) per the FeatureDomain architecture.
"""

from ...core_subsystems.context_selection_service import ContextSelectionService as _CSS


def normalize_weights_in_storage(ctrl, vertex_index, active_vg_name, layer_dict):
    """Bridge: harvest bpy context then delegate to ContextSelectionService."""
    return _CSS.normalize_weights(
        layer_dict=layer_dict,
        vertex_index=vertex_index,
        active_vg_name=active_vg_name,
        vg_names=[vg.name for vg in ctrl.obj.vertex_groups],
        bone_locks=ctrl.get_bone_locks(),
        is_mask=ctrl._is_mask_context(),
    )
