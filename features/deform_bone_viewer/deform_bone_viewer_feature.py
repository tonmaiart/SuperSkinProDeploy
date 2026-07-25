"""DeformBoneViewerFeature — Unified Component Architecture implementation for the deform_bone_viewer domain.

Collapses the old DeformBoneViewerDomain (action dispatch) and prefs.py (draw,
persistence) into a single UnifiedFeatureExtension subclass.

This is a non-collapsible viewer domain that renders the Deform Bone List at the
top of the SKINNING tab at full width.
"""

import os
import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import ui


_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# DeformBoneViewerFeature — UnifiedFeatureExtension
# ==============================================================================

class DeformBoneViewerFeature(UnifiedFeatureExtension):
    """Non-collapsible viewer extension for the Deform Bone List in the SKINNING tab."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "deform_bone_viewer"
    actions = []
    section_title = "Deform Bones List"
    draw_tab = "SKINNING"
    collapsible = True
    priority = 0
    expanded_by_default = True
    locked_expanded = True
    keymaps = [
        {"key": "Alt+Ctrl+RMB", "label": "Select Affected Boundary"},
    ]

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Render the Deform Bone List, then a bottom row with the active
        layer name label followed by the Save Weights & Exit button.

        No inner ``box()`` here (removed) -- drawing straight into *layout*
        so this section looks flat/consistent with the other
        ``locked_expanded`` sections (weight_apply, mirror, etc.), none of
        which wrap themselves in their own box.

        No mesh-name label anymore (used to sit in its own meta-data row
        above the list, alongside the layer name) -- the layer name alone
        is enough context, now placed directly in front of the Save
        Weights & Exit button instead of its own separate row.
        """
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            layout.label(text="No mesh active", icon='ERROR')
            return

        layer_name = "—"
        if "ss_layers_meta" in obj.data:
            try:
                layer_name = CoreFacade(context).active_layer_name()
            except ValueError:
                pass  # Not activated -- shouldn't reach here, but stay graceful.

        ui.draw_influence_list_system(layout, context, rows=7)
        layout.separator(factor=0.4)
        row = layout.row()
        row.scale_y = 1.4
        row.label(text=layer_name, icon='RENDERLAYERS')
        row.operator("superskin.save_weight_and_exit", icon='IMPORT')

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register the feature with UnifiedRegistry."""
    UnifiedRegistry.register(DeformBoneViewerFeature())


def unregister():
    """Unregister the feature from UnifiedRegistry."""
    UnifiedRegistry.unregister("deform_bone_viewer")
