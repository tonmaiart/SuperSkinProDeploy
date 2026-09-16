"""ControllerFeature — Unified Component Architecture implementation for the controller domain.

Collapses the old ControllerDomain (action dispatch) into a single
UnifiedFeatureExtension subclass.

This is a structural-only domain with no draw_section content in the N-panel.
All runtime behaviour (scene-mode switching, pie menu, safe-shrink) is
registered directly from __init__.py via the ops_* sub-modules.
"""

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade


# ==============================================================================
# ControllerFeature — UnifiedFeatureExtension
# ==============================================================================

class ControllerFeature(UnifiedFeatureExtension):
    """Structural extension for the Controller domain (scene-mode gate, pie menu, utilities)."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "controller"
    actions = []
    section_title = "Controller"
    draw_tab = ""  # No N-panel UI — kept out of every tab's draw loop entirely.
    keymaps = [
        {"key": "Alt+Shift+Scroll", "label": "Fast Timeline Scrub"},
    ]

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Structural-only domain — no N-panel UI content."""
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
    """Register the feature with UnifiedRegistry."""
    UnifiedRegistry.register(ControllerFeature())


def unregister():
    """Unregister the feature from UnifiedRegistry."""
    UnifiedRegistry.unregister("controller")
