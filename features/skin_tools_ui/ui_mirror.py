
import bpy



class SUPERSKIN_UL_mirror_sr(bpy.types.UIList):
    bl_idname = "SUPERSKIN_UL_mirror_sr"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "search_text", text="")
        row.prop(item, "replace_text", text="")


def _draw_sr_body(box, context, mirror) -> None:
    sr_coll = mirror.search_replace_pairs
    idx = mirror.search_replace_index

    row = box.row()
    row.template_list(
        "SUPERSKIN_UL_mirror_sr", "",
        mirror, "search_replace_pairs",
        mirror, "search_replace_index",
        rows=4,
    )

    col_btns = row.column(align=True)
    col_btns.operator("superskin.add_mirror_sr", text="", icon='ADD')
    remove_col = col_btns.column(align=True)
    remove_col.enabled = 0 <= idx < len(sr_coll)
    rm = remove_col.operator("superskin.remove_mirror_sr", text="", icon='REMOVE')
    rm.index = idx



class SUPERSKIN_PT_mirror_options(bpy.types.Panel):
    """Mirror weights from one side of the mesh to the other"""
    bl_idname = "SUPERSKIN_PT_mirror_options"
    bl_label = "Mirror Weight"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 16

    def draw(self, context):
        layout = self.layout
        mirror = context.window_manager.superskin_mirror_prefs

        col_opts = layout.column(align=True)
        row_axis = col_opts.split(factor=0.45, align=True)
        row_axis.label(text="Mirror Axis:")
        row_axis.prop(mirror, "mirror_axis", text="")
        col_opts.separator(factor=0.5)
        row_dir = col_opts.split(factor=0.45, align=True)
        row_dir.label(text="Mirror Direction:")
        row_dir.prop(mirror, "direction", text="")
        col_opts.separator(factor=0.5)
        row_data = col_opts.split(factor=0.45, align=True)
        row_data.label(text="Mirror Target Data:")
        row_data.prop(mirror, "mirror_data", text="")
        col_opts.separator(factor=0.5)
        layout.label(text="Mapping Keywords:")
        _draw_sr_body(layout.box(), context, mirror)
        layout.separator(factor=0.5)
        info = layout.column(align=True)
        info.scale_y = 0.8
        info.label(text="Tip: select several layers in the Layer list", icon='INFO')
        info.label(text="to mirror them all at once.", icon='BLANK1')
        layout.separator(factor=0.5)
        layout.operator("object.mirror_weights", text="Mirror Weight")



_classes = (
    SUPERSKIN_UL_mirror_sr,
    SUPERSKIN_PT_mirror_options,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
