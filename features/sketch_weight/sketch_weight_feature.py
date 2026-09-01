"""SketchWeightFeature -- Unified Component Architecture implementation for
the Sketch Weight Guide domain (multi-bone inverse-LBS from a drawn stroke).

Owns:
  - SSPrefSketchWeight PropertyGroup (registered on WindowManager)
  - Action dispatch: "draw_guide_stroke" -- forwards to the modal operator
  - UI layout: draw_section() -- the "Draw Weight Guide" button + 1 control
  - JSON persistence: populate() / serialize_into()

See ``docs/domains/sketch_weight.md`` for architecture, dataflow, and
guardrails.
"""

import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# Property Groups
# ==============================================================================

def _on_changed(self, context):
    from ...core.facade import CoreFacade
    CoreFacade.save_prefs()


class SSPrefSketchWeight(bpy.types.PropertyGroup):
    guide_radius: bpy.props.IntProperty(
        name="Guide Radius",
        description=(
            "Screen-space distance (pixels) from the stroke a vertex must be within to be "
            "affected"
        ),
        min=4, max=200, default=40, update=_on_changed,
    )


# ==============================================================================
# SketchWeightFeature -- UnifiedFeatureExtension
# ==============================================================================

class SketchWeightFeature(UnifiedFeatureExtension):
    """Unified extension for the Sketch Weight Guide domain."""

    domain_id = "sketch_weight"
    actions = ["draw_guide_stroke"]
    section_title = "Sketch Weight Guide"
    draw_tab = "SKINNING"
    defaults_path = _DEFAULTS_PATH

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        if action == "draw_guide_stroke":
            # Only activate the toolbar's "Sketch Weight Guide" tool --
            # do NOT also invoke mesh.ssp_sketch_guide_draw here. That
            # operator's modal starts recording MOUSEMOVE samples
            # unconditionally (it assumes the tool's own LEFTMOUSE-PRESS
            # keymap already means the button is held); invoking it from a
            # plain button click starts the modal with no mouse button
            # actually down, so the very next cursor movement toward the
            # viewport was being recorded as if it were a real drag,
            # producing an unintended stroke. The user presses LEFTMOUSE in
            # the viewport themselves once the tool is active, same as
            # clicking the toolbar icon directly.
            try:
                bpy.ops.wm.tool_set_by_id(name="superskin.sketch_weight_guide")
            except Exception:
                pass
            return {"status": "FINISHED"}
        return {"status": "CANCELLED", "message": f"Unknown action: {action}"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        prefs = context.window_manager.superskin_sketch_weight_prefs
        col = layout.column(align=True)
        col.label(text="Select the tool below, then drag a stroke")
        col.label(text="across the mesh in the viewport.")
        col.separator()
        op = col.operator("superskin.execute_action", text="Select Sketch Guide Tool", icon='GREASEPENCIL')
        op.domain_id = "sketch_weight"
        op.action_id = "draw_guide_stroke"
        col.separator()
        col.prop(prefs, "guide_radius")

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        prefs = bpy.context.window_manager.superskin_sketch_weight_prefs
        if "guide_radius" in data:
            prefs.guide_radius = int(data["guide_radius"])

    def serialize_into(self, full_dict: dict) -> None:
        prefs = bpy.context.window_manager.superskin_sketch_weight_prefs
        full_dict["sketch_weight"] = {
            "guide_radius": prefs.guide_radius,
        }


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    bpy.utils.register_class(SSPrefSketchWeight)
    bpy.types.WindowManager.superskin_sketch_weight_prefs = bpy.props.PointerProperty(
        type=SSPrefSketchWeight, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(SketchWeightFeature())


def unregister():
    UnifiedRegistry.unregister("sketch_weight")
    try:
        del bpy.types.WindowManager.superskin_sketch_weight_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefSketchWeight)
