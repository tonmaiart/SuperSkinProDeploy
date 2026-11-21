
import bpy

from ....interface.utils.icons import get_select_vertices_icon_id
from ..ui_tips import draw_tip


def _draw_select_op(layout, op_id, text, icon_value, fallback_icon):
    if icon_value:
        layout.operator(op_id, text=text, icon_value=icon_value)
    else:
        layout.operator(op_id, text=text, icon=fallback_icon)


class SUPERSKIN_PT_select_vertices_options(bpy.types.Panel):
    """Select or deselect the vertices affected by the selected bones or the active layer's mask"""
    bl_idname = "SUPERSKIN_PT_select_vertices_options"
    bl_label = "Select Vertices"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 10

    def draw(self, context):
        obj = context.active_object
        storage = getattr(obj, "superskin_storage", None) if obj else None
        if storage is not None and storage.active_is_mask:
            op_fmt = "superskin.layer_{}_vertices"
        else:
            op_fmt = "superskin.{}_by_selected_bones"

        for affected, word in ((True, "affected"), (False, "unaffected")):
            col = self.layout.column(align=True)
            for add, verb in ((True, "select"), (False, "deselect")):
                _draw_select_op(col, op_fmt.format(f"{verb}_{word}"), f"{verb.title()} {word.title()}",
                                get_select_vertices_icon_id(add, affected),
                                'RESTRICT_SELECT_OFF' if add else 'RESTRICT_SELECT_ON')
            self.layout.separator(factor=0.5)
        draw_tip(self.layout, ("Tip: Hold Shift while clicking", "to add to your current selection."))


def register():
    bpy.utils.register_class(SUPERSKIN_PT_select_vertices_options)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_PT_select_vertices_options)
