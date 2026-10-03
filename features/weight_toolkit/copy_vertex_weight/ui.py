
import bpy


class SUPERSKIN_PT_copy_vertex_options(bpy.types.Panel):
    """Copy weights from one vertex and paste them onto others"""
    bl_idname = "SUPERSKIN_PT_copy_vertex_options"
    bl_label = "Copy Vertex"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 12

    def draw(self, context):
        layout = self.layout
        layout.operator("object.ssp_copy_weight_single", text="Copy Vertex")
        layout.operator("object.ssp_paste_weight_replace", text="Paste Vertex")


def register():
    bpy.utils.register_class(SUPERSKIN_PT_copy_vertex_options)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_PT_copy_vertex_options)
