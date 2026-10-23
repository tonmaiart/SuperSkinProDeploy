
import bpy

from .common import LASSO_TOOL_IDNAME, poll_session_mesh


class SUPERSKIN_OT_lasso_tool_activate(bpy.types.Operator):
    """Switch to the Lasso Select tool"""
    bl_idname = "superskin.lasso_tool_activate"
    bl_label = "Activate Lasso Select"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return poll_session_mesh(context)

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
