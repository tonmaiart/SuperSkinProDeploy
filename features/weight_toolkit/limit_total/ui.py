
import bpy


class SUPERSKIN_PT_limit_total_options(bpy.types.Panel):
    """Limit how many bones can influence each vertex"""
    bl_idname = "SUPERSKIN_PT_limit_total_options"
    bl_label = "Limit Total Influences"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 10

    def draw(self, context):
        layout = self.layout
        layout.prop(context.window_manager, "superskin_limit_total_max_influences",
                    text="Max Influences")
        layout.separator(factor=0.5)
        layout.operator("mesh.ssp_limit_total_select_exceeded", text="Select Exceeded Vertices")
        layout.operator("mesh.ssp_limit_total_apply", text="Limit Total")
        layout.operator("mesh.ssp_normalize_layer", text="Normalize")


def register():
    bpy.utils.register_class(SUPERSKIN_PT_limit_total_options)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_PT_limit_total_options)
