"""AutoBlockFeature — Unified Component Architecture for auto block weight assignment.

Collapses the old AutoBlockDomain (action dispatch) and inline PrefsExtensionSpec
into a single UnifiedFeatureExtension subclass.

Owns:
  - Action dispatch: "auto"
  - UI layout: delegates to .ui.draw_section()
  - JSON persistence: no-op (this domain has no user-editable prefs)
"""

import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import ui

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# AutoBlockFeature — UnifiedFeatureExtension
# ==============================================================================

class AutoBlockFeature(UnifiedFeatureExtension):
    """Unified extension for the Auto Block Weight domain."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "auto_block"
    actions = ["auto"]
    section_title = "Auto Assign"
    draw_tab = "SKINNING"
    defaults_path = _DEFAULTS_PATH
    json_path = ("auto_block_weight",)
    priority = 2
    expanded_by_default = True
    locked_expanded = True

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        from .logic import apply, gather_auto_bone_data

        if core_facade.is_mask_context():
            return {"status": "CANCELLED",
                    "message": "Auto Assign not available in mask mode"}

        obj = core_facade.get_obj()
        arm_obj = next(
            (m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None
        )
        if not arm_obj:
            return {"status": "CANCELLED", "message": "No armature modifier found"}

        bone_data, bone_name_to_name = gather_auto_bone_data(core_facade, arm_obj)

        ctrl = core_facade.get_ctrl()
        bvh = ctrl.storage.build_bvh_tree()
        mat = obj.matrix_world
        world_coords = [(mat @ v.co).to_tuple() for v in ctrl.mesh.vertices]

        assignment = apply(
            selected_verts=ctrl.get_selected_verts(),
            vertex_world_coords=world_coords,
            bone_data=bone_data,
            bone_name_to_name=bone_name_to_name,
            bvh_tree=bvh,
            neighbors=core_facade.get_cached_mesh_neighbors(),
        )

        layer_dict = core_facade.read_active_layer()

        pool_bone_names = set(bone_name_to_name.values())

        for v_idx, best_bone_name in assignment.items():
            layer_dict.setdefault(v_idx, {})
            for b_name in pool_bone_names:
                layer_dict[v_idx].pop(b_name, None)
            layer_dict[v_idx][best_bone_name] = 1.0
            layer_dict = core_facade.normalize_weights(
                layer_dict, v_idx, best_bone_name
            )

        core_facade.write_active_layer(layer_dict, color_only=True)
        return {"status": "FINISHED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Draw the Auto Assign section — delegates to .ui.draw_section()."""
        ui.draw_section(layout)

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register with UnifiedRegistry."""
    UnifiedRegistry.register(AutoBlockFeature())


def unregister():
    """Unregister from UnifiedRegistry."""
    UnifiedRegistry.unregister("auto_block")
