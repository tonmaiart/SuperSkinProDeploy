"""CircleToolAdjustFeature — Unified Component Architecture for circle brush radius control.

Collapses the old CircleToolAdjustDomain (action dispatch) and prefs.py
(PropertyGroup, draw, persistence) into a single UnifiedFeatureExtension subclass.

Owns:
  - SSPrefCircleToolAdjust PropertyGroup (registered on WindowManager)
  - Action dispatch: "adjust_radius_interactive"
  - JSON persistence: populate() / serialize_into()

No N-panel UI (draw_tab="") — the brush radius is adjusted via the
interactive modal operator, not a sidebar slider.
"""

import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# Property Groups
# ==============================================================================

def _on_radius_updated(self, context):
    """Sync the new radius to Blender's native select_circle tool properties."""
    if context is None or context.workspace is None:
        return
    tool = context.workspace.tools.from_space_view3d_mode('EDIT_MESH', create=False)
    if tool:
        try:
            props = tool.operator_properties("view3d.select_circle")
            if props:
                props.radius = max(1, min(self.brush_radius_value, 300))
        except Exception:
            pass


class SSPrefCircleToolAdjust(bpy.types.PropertyGroup):
    brush_radius_value: bpy.props.IntProperty(
        name="Brush Radius",
        description="Current radius value used for circle brush calculations",
        min=1,
        max=300,
        default=30,
        update=_on_radius_updated,
    )


def get_prefs() -> SSPrefCircleToolAdjust:
    """Convenience accessor for the WindowManager property."""
    return bpy.context.window_manager.superskin_circle_tool_adjust_prefs


# ==============================================================================
# CircleToolAdjustFeature — UnifiedFeatureExtension
# ==============================================================================

class CircleToolAdjustFeature(UnifiedFeatureExtension):
    """Unified extension for the Circle Tool Adjust domain."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "circle_tool_adjust"
    actions = ["adjust_radius_interactive"]
    section_title = "Circle Brush Adjust"
    draw_tab = ""  # No N-panel UI — draw_section() is never invoked by UnifiedRegistry.get_by_tab().
    defaults_path = _DEFAULTS_PATH
    keymaps = [
        {"key": "Alt+Shift+RMB", "label": "Circle Radius / Toggle Select Tool", "mode": "Hold"},
        {"key": "Alt+Ctrl+Scroll", "label": "Grow/Shrink Selection"},
    ]

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        if action == "adjust_radius_interactive":
            bpy.ops.superskin.circle_tool_adjust_radius('INVOKE_DEFAULT')
            return {"status": "FINISHED"}
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
        get_prefs().brush_radius_value = int(data.get("default_radius", 30))

    def serialize_into(self, full_dict: dict) -> None:
        """Write current values into full_dict at the correct JSON path."""
        full_dict["circle_tool_adjust"] = {
            "default_radius": get_prefs().brush_radius_value,
        }


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

_classes = (SSPrefCircleToolAdjust,)


def register():
    """Register PropertyGroup on WindowManager and the extension with UnifiedRegistry."""
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.superskin_circle_tool_adjust_prefs = bpy.props.PointerProperty(
        type=SSPrefCircleToolAdjust, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(CircleToolAdjustFeature())


def unregister():
    """Unregister PropertyGroup and the extension."""
    UnifiedRegistry.unregister("circle_tool_adjust")
    try:
        del bpy.types.WindowManager.superskin_circle_tool_adjust_prefs
    except Exception:
        pass
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
