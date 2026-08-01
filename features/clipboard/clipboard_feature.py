# Copyright (c) 2026 Natchapon Srisuk. All rights reserved.
"""ClipboardFeature — Unified Component Architecture implementation for the clipboard domain.

Collapses the old ClipboardDomain (action dispatch) and prefs.py (draw,
persistence) into a single UnifiedFeatureExtension subclass.

Owns:
  - SSPrefClipboard PropertyGroup (registered on WindowManager) -- purely
    UI-state (which tab is active, which paste mode the Plane Copy tab's
    single Paste button uses), not a persisted setting.
  - Action dispatch: copy, cut, paste_add, paste_subtract, paste_replace,
    copy_whole, copy_single, select_affected
  - UI layout: draw_section() — a tab switcher (Plane Copy / Vertex
    Influence Copy) over one section at a time, both context-driven (no
    manual BONE/LAYER dropdown).
  - JSON persistence: populate() / serialize_into() (no-ops, no persistent
    settings)
"""

import os
import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade


# ==============================================================================
# Property Groups
# ==============================================================================

class SSPrefClipboard(bpy.types.PropertyGroup):
    """Session-only UI state for the Clipboard Manager panel -- which tab is
    showing, and which mode the Plane Copy tab's single Paste button uses.
    Not a persisted preference (SKIP_SAVE on the WindowManager pointer)."""

    active_tab: bpy.props.EnumProperty(
        name="Clipboard Tab",
        items=[
            ('PLANE', "Plane Copy", "Copy/paste the whole active Vertex Group or mask across the entire mesh", 'FOLDER_REDIRECT', 0),
            ('VERTEX', "Vertex Influence Copy", "Copy/paste one vertex's full influence snapshot", 'VERTEXSEL', 1),
        ],
        default='PLANE',
    )

    plane_paste_mode: bpy.props.EnumProperty(
        name="Paste Mode",
        items=[
            ('ADD', "Add", "Merge values up to a ceiling of 1.0", 'ADD', 0),
            ('SUBTRACT', "Subtract", "Deduct values, flooring at 0.0", 'REMOVE', 1),
            ('REPLACE', "Replace", "Overwrite the active Vertex Group/mask's values completely", 'PASTEDOWN', 2),
        ],
        default='REPLACE',
    )


# ==============================================================================
# ClipboardFeature — UnifiedFeatureExtension
# ==============================================================================

class ClipboardFeature(UnifiedFeatureExtension):
    """Unified extension for the Clipboard domain."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "clipboard"
    actions = ["copy", "cut", "paste_add", "paste_subtract", "paste_replace",
               "copy_whole", "copy_single", "select_affected"]
    section_title = "Clipboard Manager"
    draw_tab = "SKINNING"
    defaults_path = os.path.join(os.path.dirname(__file__), "default_config.json")
    collapsible = True
    expanded_by_default = True
    locked_expanded = True  # non-collapsible section -- always shown, no clickable header toggle

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        from .logic import copy, cut, paste, copy_whole, copy_single_vertex, select_affected
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
            elif action == "copy_whole":
                copy_whole(core_facade)
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
        """Everything in a single row: icon-only Plane/Vertex tab toggle,
        then Copy, then (Plane tab only) a real dropdown for the Add/
        Subtract/Replace paste mode, then Paste -- three visually distinct
        button clusters (tab / copy / paste) on one line, not stacked rows.

        Vertex Influence Copy has no mode dropdown -- it only ever
        replaces (stamps one vertex's exact influence onto others), so a
        mode selector would be meaningless there.
        """
        layout.use_property_decorate = False
        prefs = context.window_manager.superskin_clipboard_prefs

        row = layout.row(align=True)
        row.scale_y = 1.2

        tab_group = row.row(align=True)
        tab_group.prop(prefs, "active_tab", expand=True, icon_only=True)

        row.separator()  # only gap in the row -- everything after this stays fused

        if prefs.active_tab == 'PLANE':
            row.operator("object.ssp_copy_weight_whole", text="Copy", icon='COPYDOWN')
            row.prop(prefs, "plane_paste_mode", text="")
            row.operator("object.ssp_paste_weight_plane", text="Paste", icon='PASTEDOWN')
        else:
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
    """Register PropertyGroups on WindowManager and the extension with UnifiedRegistry."""
    bpy.utils.register_class(SSPrefClipboard)
    bpy.types.WindowManager.superskin_clipboard_prefs = bpy.props.PointerProperty(
        type=SSPrefClipboard, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(ClipboardFeature())


def unregister():
    """Unregister PropertyGroups and the extension."""
    UnifiedRegistry.unregister("clipboard")
    try:
        del bpy.types.WindowManager.superskin_clipboard_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefClipboard)
