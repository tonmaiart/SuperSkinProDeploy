
import bpy

from .. import ADDON_NAME
from .widget_preferences import draw_dev_debugger_body

_IS_DEV_BUILD = "Dev" in ADDON_NAME


class VIEW3D_PT_superskin_dev_debugger(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_superskin_dev_debugger"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = ADDON_NAME
    bl_label = "Dev Debugger"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 1000001

    @classmethod
    def poll(cls, context):
        return _IS_DEV_BUILD and context.window_manager.superskin_show_dev_debugger

    def draw(self, context):
        draw_dev_debugger_body(self.layout, context)


def register():
    if not _IS_DEV_BUILD:
        return
    bpy.types.WindowManager.superskin_show_dev_debugger = bpy.props.BoolProperty(
        name="Show Dev Debugger",
        description="Show the Dev Debugger panel in the sidebar",
        default=False,
    )
    bpy.utils.register_class(VIEW3D_PT_superskin_dev_debugger)


def unregister():
    if not _IS_DEV_BUILD:
        return
    bpy.utils.unregister_class(VIEW3D_PT_superskin_dev_debugger)
    del bpy.types.WindowManager.superskin_show_dev_debugger
