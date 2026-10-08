
import bpy

from .logic import marked_source_count_for_mesh

from ..ui_tips import draw_tip


class SUPERSKIN_PT_self_transfer_options(bpy.types.Panel):
    """Copy weights from one part of the mesh to another"""
    bl_idname = "SUPERSKIN_PT_self_transfer_options"
    bl_label = "Self Transfer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 12

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        mesh_name = obj.data.name if obj and obj.type == 'MESH' else None
        marked_count = marked_source_count_for_mesh(mesh_name)

        layout.operator(
            "mesh.ssp_inmesh_mark_source", text="Mark Source",
            depress=marked_count > 0,
        )
        transfer_row = layout.row(align=True)
        transfer_row.enabled = marked_count > 0
        transfer_row.operator("mesh.ssp_inmesh_transfer", text="Transfer")
        layout.separator(factor=0.5)
        draw_tip(layout, ("Tip: select the vertices to copy", "from, then click Mark Source.", "Select targets and click Transfer."))


def register():
    bpy.utils.register_class(SUPERSKIN_PT_self_transfer_options)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_PT_self_transfer_options)
