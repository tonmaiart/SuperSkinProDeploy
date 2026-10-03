
import bpy

from ..weight_transfer.public_api import sync_active_entry_from_viewport



class SUPERSKIN_UL_wt_entries(bpy.types.UIList):
    """One row per entry in the unified Source/Target list: mesh name, Source toggle,
    and selected-vertices toggle."""
    bl_idname = "SUPERSKIN_UL_wt_entries"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        if item.object:
            row.label(text=item.object.name, icon='MESH_DATA')
        else:
            row.label(text="(missing mesh)", icon='ERROR')
        source_icon = 'CHECKBOX_HLT' if item.is_source else 'CHECKBOX_DEHLT'
        row.prop(item, "is_source", text="", icon=source_icon, toggle=True)
        toggle_icon = 'VERTEXSEL' if item.use_selected_verts else 'OBJECT_DATA'
        row.prop(item, "use_selected_verts", text="", icon=toggle_icon, toggle=True)



class SUPERSKIN_PT_weight_transfer_options(bpy.types.Panel):
    """Transfer weights between meshes"""
    bl_idname = "SUPERSKIN_PT_weight_transfer_options"
    bl_label = "Transfer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 14

    def draw(self, context):
        layout = self.layout
        _draw_entries(layout, context)
        layout.separator(factor=0.5)
        _draw_options(layout, context)
        layout.separator(factor=0.5)
        layout.operator("object.mw_copy_skin_weight_transfer", text="Transfer Weight")


def _draw_options(layout, context):
    prefs = context.window_manager.superskin_weight_transfer_prefs
    col = layout.column(align=True)
    row = col.split(factor=0.4, align=True)
    row.label(text="Method:")
    row.prop(prefs, "transfer_method", text="")
    col.prop(prefs, "keep_old_layer_data")


class SUPERSKIN_PT_weight_import(bpy.types.Panel):
    """Import weights from a JSON file"""
    bl_idname = "SUPERSKIN_PT_weight_import"
    bl_label = "Import"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 14

    def draw(self, context):
        layout = self.layout
        _draw_options(layout, context)
        layout.separator(factor=0.5)
        layout.operator("superskin.import_weight_json", text="Import")



def _draw_entries(layout, context):
    sync_active_entry_from_viewport(context)
    state = context.scene.superskin_weight_transfer_state

    row = layout.row()
    row.template_list(
        "SUPERSKIN_UL_wt_entries", "", state, "entries", state, "active_entry_index", rows=4,
    )
    col = row.column(align=True)
    col.operator("superskin.wt_add_entry", text="", icon='ADD')
    col.operator("superskin.wt_remove_entry", text="", icon='REMOVE')



def draw_import_export_section(layout, context):
    grid = layout.grid_flow(
        row_major=True, columns=3, even_columns=True, even_rows=True, align=True,
    )
    grid.scale_y = 1.2

    grid.operator("wm.call_panel", text="Transfer...").name = SUPERSKIN_PT_weight_transfer_options.bl_idname
    grid.operator("wm.call_panel", text="Import...").name = SUPERSKIN_PT_weight_import.bl_idname
    grid.operator("superskin.export_weight_json", text="Export")



_classes = (
    SUPERSKIN_UL_wt_entries,
    SUPERSKIN_PT_weight_transfer_options,
    SUPERSKIN_PT_weight_import,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
