"""WorkSpaceTool registration for Sketch Weight Guide -- adds a toolbar
entry (T-shelf) shown only in Edit Mesh mode.

While this tool is active, a plain LEFTMOUSE press+drag+release in the
viewport directly invokes ``mesh.ssp_sketch_guide_draw`` via the tool's own
``bl_keymap``. Blender's tool system guarantees this starts with correct
VIEW_3D WINDOW region context -- unlike invoking the same operator from the
N-panel's "Draw Weight Guide" button, which needs its own region-resolution
workaround (see ``ops.py``'s ``invoke()`` comment). This tool is the
robust, primary way to draw a guide stroke; the N-panel button remains as a
secondary convenience entry point.
"""

import bpy
from bpy.types import WorkSpaceTool


class SUPERSKIN_TOOL_sketch_weight_guide(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'EDIT_MESH'
    bl_idname = "superskin.sketch_weight_guide"
    bl_label = "Sketch Weight Guide"
    bl_description = (
        "Draw a guide stroke on the mesh surface; on release, solves "
        "multi-bone weights so nearby vertices follow the drawn silhouette"
    )
    bl_icon = "ops.gpencil.draw"
    bl_widget = None
    bl_keymap = (
        ("mesh.ssp_sketch_guide_draw", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
    )

    def draw_settings(context, layout, tool):
        prefs = context.window_manager.superskin_sketch_weight_prefs
        layout.prop(prefs, "guide_radius")


def register():
    bpy.utils.register_tool(
        SUPERSKIN_TOOL_sketch_weight_guide,
        after={"builtin.select_box"},
        separator=True,
    )


def unregister():
    bpy.utils.unregister_tool(SUPERSKIN_TOOL_sketch_weight_guide)
