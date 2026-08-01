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
        # "Select Affected Boundary" (Alt+Ctrl+RMB) entry temporarily
        # hidden per user request -- the shortcut itself was already
        # reclaimed by features/weight_apply for the Smooth/Sharpen
        # fine-precision gesture (see that domain's keymap.py/README), so
        # advertising it here in the shortcut-overlay HUD was stale/
        # misleading. The mw_select_affect_boundary operator remains fully
        # registered; re-adding the entry below restores the HUD line.
        # {"key": "Alt+Ctrl+RMB", "label": "Select Affected Boundary"},
        {"key": "Alt+1", "label": "Toggle Mask Mode", "mode": "Toggle"},
    ]

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Render the Deform Bone List, then a bottom row with the "Mask"
        toggle button and the Save Weights & Exit button.

        No inner ``box()`` here (removed) -- drawing straight into *layout*
        so this section looks flat/consistent with the other
        ``locked_expanded`` sections (weight_apply, mirror, etc.), none of
        which wrap themselves in their own box.

        No standalone layer-name label anymore -- the "Mask" toggle button
        (``superskin.toggle_mask_mode``) now doubles as that label, drawn
        with the active layer's own text and icon (``layer_name`` /
        ``RENDERLAYERS``) instead of a fixed "Mask" caption. Its pressed
        (``depress``) state still communicates whether Mask edit mode is
        currently active.
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

        row.operator(
            "superskin.toggle_mask_mode", text=layer_name, icon='RENDERLAYERS',
            depress=obj.superskin_storage.active_is_mask,
        )
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
