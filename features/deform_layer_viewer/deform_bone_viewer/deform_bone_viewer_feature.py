"""DeformBoneViewerFeature — Unified Component Architecture implementation for the
deform_bone_viewer domain."""

import os
import bpy

from ....interface.registry.register_api import UnifiedFeatureExtension
from ....core.facade import CoreFacade
from . import ui


_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# DeformBoneViewerFeature — UnifiedFeatureExtension
# ==============================================================================

class DeformBoneViewerFeature(UnifiedFeatureExtension):
    """Non-collapsible viewer extension for the Deform Bone List in the SKINNING tab."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "deform_bone_viewer"
    actions = [
        "copy_bone_plane", "cut_bone_plane", "paste_bone_plane_add", "paste_bone_plane_subtract", "paste_bone_plane_replace",
        "copy_layer_plane", "cut_layer_plane", "paste_layer_plane_add", "paste_layer_plane_subtract", "paste_layer_plane_replace",
    ]
    section_title = "Deform Bones List"
    draw_tab = "SKINNING"
    link = "https://docs.superskinpro.com/bone_list/"
    collapsible = True
    priority = 0
    expanded_by_default = True
    locked_expanded = True
    show_section_label = False
    keymaps = [
        {"key": "Ctrl+I", "label": "Invert Bone Selection"},
        {"key": "Ctrl+I", "label": "Invert Layer Selection"},
    ]

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        """Dispatches the Deform Bones List's two Plane-Copy clipboard action groups
        ("Clipboard Bone Weight" /."""
        from . import clipboard_logic
        try:
            if action == "copy_bone_plane":
                clipboard_logic.bone_weight_clipboard.copy(core_facade)
            elif action == "cut_bone_plane":
                clipboard_logic.bone_weight_clipboard.cut(core_facade)
            elif action == "paste_bone_plane_add":
                clipboard_logic.bone_weight_clipboard.paste(core_facade, mode='ADD')
            elif action == "paste_bone_plane_subtract":
                clipboard_logic.bone_weight_clipboard.paste(core_facade, mode='SUBTRACT')
            elif action == "paste_bone_plane_replace":
                clipboard_logic.bone_weight_clipboard.paste(core_facade, mode='REPLACE')
            elif action == "copy_layer_plane":
                clipboard_logic.layer_weight_clipboard.copy(core_facade)
            elif action == "cut_layer_plane":
                clipboard_logic.layer_weight_clipboard.cut(core_facade)
            elif action == "paste_layer_plane_add":
                clipboard_logic.layer_weight_clipboard.paste(core_facade, mode='ADD')
            elif action == "paste_layer_plane_subtract":
                clipboard_logic.layer_weight_clipboard.paste(core_facade, mode='SUBTRACT')
            elif action == "paste_layer_plane_replace":
                clipboard_logic.layer_weight_clipboard.paste(core_facade, mode='REPLACE')
            else:
                return {"status": "CANCELLED", "message": f"Unknown action: {action}"}
        except ValueError as e:
            return {"status": "CANCELLED", "message": str(e)}
        return {"status": "FINISHED"}

    def get_keymap_items(self) -> list:
        """Expose Alt+1's ``(km, kmi, label)`` to the in-panel shortcut editor
        (``interface/utils/keymap_editor.py``)."""
        from . import keymap as _keymap
        return _keymap.get_registered_keymap_items()

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Draw just the Deform Bones list, on its own, with no embedded Layer list and no
        bottom widget row."""
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            layout.label(text="No mesh active", icon='ERROR')
            return

        ui.draw_influence_list_system(layout, context, rows=7)

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


# ==============================================================================
# Registration
# ==============================================================================
#
# This class is no longer registered with UnifiedRegistry directly -- it and
# its `layer_viewer` counterpart were merged into one domain,
# `deform_layer_viewer`, registered under both the LAYER and SKINNING tabs
# (see docs/domains/deform_layer_viewer.md). `deform_layer_viewer_feature.py`
# (the package parent) instantiates this class and delegates execute()/
# draw_section() to it for the SKINNING tab; no `register()`/`unregister()`
# function is needed here anymore.
