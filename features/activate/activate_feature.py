"""ActivateFeature — Unified Component Architecture implementation for the activate domain."""

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

_DOCS_URL = "https://docs.superskinpro.com/installation/"


# ==============================================================================
# ActivateFeature — UnifiedFeatureExtension
# ==============================================================================

class ActivateFeature(UnifiedFeatureExtension):
    """Unified extension for the consolidated Activate/Status UI."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "activate"
    actions = []  # No dispatch actions -- draws existing operators by bl_idname only.
    section_title = "Activate"
    draw_tab = "PREFERENCE"

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_activate_prompt(self, layout, context) -> None:
        """License-entry prompt drawn by `panel_main.py`'s `_draw_skin_tab()` in place of the
        main artwork body."""
        if CoreFacade.is_system_activated():
            return
        prefs = context.window_manager.superskin_prefs
        lic = prefs.license

        box = layout.box()
        box.label(text="Please Activate License to continue!")

        key_row = box.row(align=True)
        key_row.prop(lic, "license_key", text="")
        activate_sub = key_row.row(align=True)
        activate_sub.scale_x = 0.7
        key_row.scale_y=1.4

        activate_button_row = box.row(align=True)
        activate_button_row.scale_y = 1.4
        activate_button_row.operator("superskin.activate_license", text="Activate Key")

        help_row = box.row(align=True)
        help_row.scale_y=1.2
        help_row.operator("wm.url_open", text="What is an activate key", icon='QUESTION').url = _DOCS_URL

    def draw_section(self, layout, context) -> None:
        """Kept only to satisfy `UnifiedFeatureExtension`'s abstract contract."""
        self.draw_activate_prompt(layout, context)

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
    UnifiedRegistry.register(ActivateFeature())


def unregister():
    """Unregister the feature from UnifiedRegistry."""
    UnifiedRegistry.unregister("activate")
