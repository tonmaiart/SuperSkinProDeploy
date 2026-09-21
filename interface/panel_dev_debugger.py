"""SuperSkinPro Dev Debugger — a standalone second sidebar panel."""

import bpy

from .. import ADDON_NAME
from .widget_preferences import draw_dev_debugger_body
from . import dev_skin_spike_op

_IS_DEV_BUILD = "Dev" in ADDON_NAME


class VIEW3D_PT_superskin_dev_debugger(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_superskin_dev_debugger"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = ADDON_NAME
    bl_label = "Dev Debugger"
    bl_options = {'DEFAULT_CLOSED'}
    # One above the main panel's own bl_order (1000000) so this always
    # sorts directly underneath it, regardless of registration order.
    bl_order = 1000001

    def draw(self, context):
        draw_dev_debugger_body(self.layout, context)
        box = self.layout.box()
        box.label(text="Skin Spike (Phase 0 validation)")
        dev_skin_spike_op.draw_button(box)


def register():
    if not _IS_DEV_BUILD:
        return
    bpy.utils.register_class(VIEW3D_PT_superskin_dev_debugger)
    dev_skin_spike_op.register()


def unregister():
    if not _IS_DEV_BUILD:
        return
    dev_skin_spike_op.unregister()
    bpy.utils.unregister_class(VIEW3D_PT_superskin_dev_debugger)
