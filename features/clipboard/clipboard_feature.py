# Copyright (c) 2026 Natchapon Srisuk. All rights reserved.
"""ClipboardFeature — Unified Component Architecture implementation for the clipboard domain.

Collapses the old ClipboardDomain (action dispatch) and prefs.py (draw,
persistence) into a single UnifiedFeatureExtension subclass.

Owns:
  - Action dispatch: copy, cut, paste_add, paste_subtract, paste_replace,
    copy_single, select_affected
  - UI layout: draw_section() — "Vertex Influence Copy" (Copy + Paste,
    always Replace-only, context-driven -- no manual BONE/LAYER dropdown).
    The former "Plane Copy" tab has moved to features/deform_bone_viewer
    as two independent, fixed-target clipboard clusters ("Clipboard Bone
    Weight" / "Clipboard Layer Weight") -- see
    docs/domains/deform_bone_viewer.md.
  - JSON persistence: populate() / serialize_into() (no-ops, no persistent
    settings)
"""

import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade


# ==============================================================================
# ClipboardFeature — UnifiedFeatureExtension
# ==============================================================================

class ClipboardFeature(UnifiedFeatureExtension):
    """Unified extension for the Clipboard domain."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "clipboard"
    actions = ["copy", "cut", "paste_add", "paste_subtract", "paste_replace",
               "copy_single", "select_affected"]
    section_title = "Clipboard Manager"
    link = "https://docs.superskinpro.com/copy_weight/"
    draw_tab = "SKINNING_SOCKET"  # plugged into tool_socket's SKINNING dropdown, see docs/domains/tool_socket.md
    defaults_path = os.path.join(os.path.dirname(__file__), "default_config.json")
    collapsible = True
    expanded_by_default = True
    locked_expanded = True  # non-collapsible section -- always shown, no clickable header toggle

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        from .logic import copy, cut, paste, copy_single_vertex, select_affected
        try:
            if action == "copy":
                copy(core_facade)
            elif action == "cut":
                cut(core_facade)
            elif action == "paste_add":
                paste(core_facade, mode='ADD')
            elif action == "paste_subtract":
                paste(core_facade, mode='SUBTRACT')
            elif action == "paste_replace":
                paste(core_facade, mode='REPLACE')
            elif action == "copy_single":
                copy_single_vertex(core_facade)
            elif action == "select_affected":
                return {"status": "FINISHED", "data": select_affected(core_facade)}
            else:
                return {"status": "CANCELLED", "message": f"Unknown action: {action}"}
        except ValueError as e:
            return {"status": "CANCELLED", "message": str(e)}
        return {"status": "FINISHED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """"Vertex Influence Copy" -- Copy + Paste on one row. Always
        Replace-only, no mode dropdown -- it only ever replaces (stamps
        one vertex's exact influence onto others), so an Add/Subtract mode
        selector would be meaningless here. The former "Plane Copy" tab
        (with its Add/Subtract/Replace dropdown) has moved to
        features/deform_bone_viewer -- see
        docs/domains/deform_bone_viewer.md.
        """
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("object.ssp_copy_weight_single", text="Copy", icon='COPYDOWN')
        row.operator("object.ssp_paste_weight_replace", text="Paste", icon='PASTEDOWN')

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        """No persistent preferences for the clipboard domain."""
        pass

    def serialize_into(self, full_dict: dict) -> None:
        """No persistent preferences for the clipboard domain."""
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register the extension with UnifiedRegistry."""
    UnifiedRegistry.register(ClipboardFeature())


def unregister():
    """Unregister the extension."""
    UnifiedRegistry.unregister("clipboard")
