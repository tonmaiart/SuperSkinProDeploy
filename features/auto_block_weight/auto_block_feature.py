"""AutoBlockFeature — Unified Component Architecture for auto block weight assignment."""

import array
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

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
    link = "https://docs.superskinpro.com/auto_block_weight/"
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

        with core_facade.profile_section("auto_block.gather"):
            bone_data, bone_name_to_name = gather_auto_bone_data(core_facade, arm_obj)

        ctrl = core_facade.get_ctrl()
        mat = obj.matrix_world
        selected_verts = ctrl.get_selected_verts()
        mesh_verts = ctrl.mesh.vertices
        with core_facade.profile_section("auto_block.world_coords", size=len(selected_verts)):
            selected_world_coords_flat = array.array("d", [0.0]) * (len(selected_verts) * 3)
            for out_i, v_idx in enumerate(selected_verts):
                co = mat @ mesh_verts[v_idx].co
                base = out_i * 3
                selected_world_coords_flat[base] = co.x
                selected_world_coords_flat[base + 1] = co.y
                selected_world_coords_flat[base + 2] = co.z

        with core_facade.profile_section("auto_block.rust_ffi", size=len(selected_verts)):
            assignment = apply(
                selected_verts=selected_verts,
                selected_world_coords_flat=selected_world_coords_flat,
                bone_data=bone_data,
                bone_name_to_name=bone_name_to_name,
            )

        layer_dict = core_facade.read_active_layer()

        pool_bone_names = set(bone_name_to_name.values())

        with core_facade.profile_section("auto_block.normalize", size=len(assignment)):
            for v_idx, best_bone_name in assignment.items():
                vert_layer = layer_dict.setdefault(v_idx, {})
                stale = vert_layer.keys() & pool_bone_names
                stale.discard(best_bone_name)
                for b_name in stale:
                    del vert_layer[b_name]
                vert_layer[best_bone_name] = 1.0
                if len(vert_layer) > 1:
                    layer_dict = core_facade.normalize_weights(
                        layer_dict, v_idx, best_bone_name
                    )

        with core_facade.profile_section("auto_block.write", size=len(assignment)):
            core_facade.write_active_layer(
                layer_dict, color_only=True, dirty_verts=set(assignment.keys())
            )
        return {"status": "FINISHED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """UI moved to features/skin_tools_ui/ui_action_grid.py — see
        docs/domains/skin_tools_ui.md."""
        pass

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
