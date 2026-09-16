"""SUPERSKIN_OT_lasso_tool_activate -- thin shell switching the active
VIEW_3D/EDIT_MESH tool to Blender's native Lasso Select
(`lasso_tool_adjust_feature.LASSO_TOOL_IDNAME`).

Exists as its own operator (rather than inlining `wm.tool_set_by_id` at
each call site) so `lasso_tool_adjust_feature.py`'s `execute()` dispatch
has something to invoke for the `activate_lasso` action -- mirrors
`circle_tool_adjust/ops.py`'s modal operator being the thing that
domain's `execute()` invokes for `adjust_radius_interactive`.

Not what `weight_apply`'s own N-panel tool cycle button calls -- that
button (and its Alt+1 keymap counterpart) both call
`superskin.toggle_weight_brush_tool`
(`brush_tool.py::SUPERSKIN_OT_toggle_weight_brush_tool`, which imports
`LASSO_TOOL_IDNAME` from this package's `public_api.py`). This operator is
the sanctioned entry point for anything OUTSIDE that toggle (the generic
`superskin.execute_action` dispatch, a future pie menu entry, a future
keymap).
"""

import bpy

from .lasso_tool_adjust_feature import LASSO_TOOL_IDNAME


class SUPERSKIN_OT_lasso_tool_activate(bpy.types.Operator):
    """Switch the active tool to Lasso Select."""
    bl_idname = "superskin.lasso_tool_activate"
    bl_label = "Activate Lasso Select"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        bpy.ops.wm.tool_set_by_id(name=LASSO_TOOL_IDNAME)
        return {'FINISHED'}


_classes = (SUPERSKIN_OT_lasso_tool_activate,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
