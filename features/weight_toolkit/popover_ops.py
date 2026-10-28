
import bpy


class SUPERSKIN_OT_open_toolkit_popover(bpy.types.Operator):
    bl_idname = "superskin.open_toolkit_popover"
    bl_label = "Open"
    bl_options = {'INTERNAL'}

    panel: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        panel_cls = getattr(bpy.types, properties.panel, None)
        return getattr(panel_cls, "__doc__", None) or ""

    def execute(self, context):
        bpy.ops.wm.call_panel(name=self.panel, keep_open=True)
        return {'FINISHED'}


def register():
    bpy.utils.register_class(SUPERSKIN_OT_open_toolkit_popover)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_OT_open_toolkit_popover)
