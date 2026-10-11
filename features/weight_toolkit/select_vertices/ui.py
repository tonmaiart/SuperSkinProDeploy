
import bpy

from ..ui_tips import draw_tip


class SUPERSKIN_PT_select_vertices_options(bpy.types.Panel):
    """Select or deselect the vertices affected by the selected bones or the active layer's mask"""
    bl_idname = "SUPERSKIN_PT_select_vertices_options"
    bl_label = "Select Vertices"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 12

    def draw(self, context):
        obj = context.active_object
        storage = getattr(obj, "superskin_storage", None) if obj else None
        if storage is not None and storage.active_is_mask:
            select_op, deselect_op = "superskin.layer_select_affected_vertices", "superskin.layer_deselect_affected_vertices"
        else:
            select_op, deselect_op = "superskin.select_affected_by_selected_bones", "superskin.deselect_affected_by_selected_bones"

        col = self.layout.column(align=True)
        col.operator(select_op, text="Select Affected Vertices", icon='RESTRICT_SELECT_OFF')
        col.operator(deselect_op, text="Deselect Affected Vertices", icon='RESTRICT_SELECT_ON')
        self.layout.separator(factor=0.5)
        draw_tip(self.layout, ("Tip: hold Shift while clicking Select", "to add to the current selection."))


def register():
    bpy.utils.register_class(SUPERSKIN_PT_select_vertices_options)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_PT_select_vertices_options)
