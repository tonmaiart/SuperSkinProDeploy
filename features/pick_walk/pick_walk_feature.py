"""PickWalkFeature — Unified Component Architecture for Maya-style pick-walk selection.

Owns:
  - SSPrefPickWalk PropertyGroup (registered on WindowManager) -- step_pixels
    sensitivity for the Alt+Ctrl+MMB hold+drag gesture.
  - No dispatch actions -- purely a keymap-triggered modal operator
    (`superskin.pick_walk`, see ops.py), same pattern as addon_updater's
    standalone update-checker operators.

No N-panel UI (draw_tab="") -- there is nothing to configure through a
sidebar; sensitivity persists via JSON like every other domain but has no
slider exposed anywhere yet.
"""

import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# Property Groups
# ==============================================================================

class SSPrefPickWalk(bpy.types.PropertyGroup):
    step_pixels: bpy.props.IntProperty(
        name="Step Distance",
        description="Mouse movement (in pixels) needed per Pick Walk hop",
        min=5,
        max=200,
        default=25,
    )


def get_prefs() -> SSPrefPickWalk:
    """Convenience accessor for the WindowManager property."""
    return bpy.context.window_manager.superskin_pick_walk_prefs


# ==============================================================================
# PickWalkFeature — UnifiedFeatureExtension
# ==============================================================================

class PickWalkFeature(UnifiedFeatureExtension):
    """Structural extension for the Pick Walk domain -- no N-panel UI, purely
    a keymap-triggered modal selection-shift gesture."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "pick_walk"
    actions = []
    section_title = "Pick Walk"
    draw_tab = ""  # No N-panel UI — draw_section() is never invoked by UnifiedRegistry.get_by_tab().
    defaults_path = _DEFAULTS_PATH
    keymaps = [
        {"key": "Alt+Ctrl+MMB", "label": "Pick Walk Selection", "mode": "Hold"},
    ]

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED", "message": f"Unknown action: {action}"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """No N-panel UI — draw_tab="" keeps this out of every tab's draw
        loop, so this is never actually invoked. Required only because
        UnifiedFeatureExtension.draw_section() is abstract."""
        pass

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        """Write section data dict into the live WindowManager property."""
        get_prefs().step_pixels = int(data.get("step_pixels", 25))

    def serialize_into(self, full_dict: dict) -> None:
        """Write current values into full_dict at the correct JSON path."""
        full_dict["pick_walk"] = {
            "step_pixels": get_prefs().step_pixels,
        }


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

_classes = (SSPrefPickWalk,)


def register():
    """Register PropertyGroup on WindowManager and the extension with UnifiedRegistry."""
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.superskin_pick_walk_prefs = bpy.props.PointerProperty(
        type=SSPrefPickWalk, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(PickWalkFeature())


def unregister():
    """Unregister PropertyGroup and the extension."""
    UnifiedRegistry.unregister("pick_walk")
    try:
        del bpy.types.WindowManager.superskin_pick_walk_prefs
    except Exception:
        pass
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
