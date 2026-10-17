
import bpy

from ...interface.template_ui import SuperSkinListMixin
from ...interface.utils.icons import get_mesh_empty_armature_icon_id, get_trash_icon_id
from ...interface.utils.popover_width import fit_width_units, open_popover_at_width
from ..deform_layer_viewer.layer_viewer.public_api import mesh_state_icon_kwargs
from ..weight_transfer.public_api import (
    can_be_source,
    entry_display_order, entry_key, entry_search_text, select_all_visible_entries,
    sync_active_entry_from_viewport,
)



_LIST_SIDE_UNITS = 2.5
_TOGGLE_ZONE_UNITS = 2.4


class SUPERSKIN_UL_wt_entries(SuperSkinListMixin, bpy.types.UIList):
    """One row per entry in the unified Source/Target list: mesh name, Source toggle and
    selected-vertices toggle."""
    bl_idname = "SUPERSKIN_UL_wt_entries"

    left_zone_factor = 0.01

    @property
    def right_zone_factor(self):
        list_units = max(SUPERSKIN_PT_weight_transfer_options.bl_ui_units_x - _LIST_SIDE_UNITS, 1.0)
        return min(_TOGGLE_ZONE_UNITS / list_units, 0.5)

    def domain(self) -> str:
        return 'WT_ENTRIES'

    def get_item_key(self, item) -> str:
        return entry_key(item)

    def get_search_text(self, item) -> str:
        return entry_search_text(item)

    def get_display_order(self, context, data):
        return entry_display_order(data.entries, self.use_filter_sort_alpha)

    def is_selected(self, context, data, key: str) -> bool:
        obj = context.view_layer.objects.get(key)
        return obj is not None and obj.select_get()

    def draw_main_icon(self, context, data, item) -> str:
        if item.object is None:
            return 'ERROR'
        return _entry_icon_kwargs(item.object).get("icon", 'NONE')

    def draw_main_icon_value(self, context, data, item) -> int:
        if item.object is None:
            return 0
        return _entry_icon_kwargs(item.object).get("icon_value", 0)

    def draw_right_icon(self, context, layout, data, item, index: int):
        source_icon = 'RADIOBUT_ON' if item.is_source else 'RADIOBUT_OFF'
        source_zone = layout.row(align=True)
        source_zone.enabled = item.is_source or can_be_source(item.object)
        _draw_flag_toggle(source_zone, index, "is_source", item.is_source, source_icon)
        toggle_icon = 'VERTEXSEL' if item.use_selected_verts else 'OBJECT_DATA'
        _draw_flag_toggle(layout, index, "use_selected_verts", item.use_selected_verts, toggle_icon)

    def get_row_operator_id(self, item=None) -> str:
        return "superskin.wt_select_entry"

    def set_row_operator_props(self, op, item):
        op.index = int(item.path_from_id().rsplit("[", 1)[1].rstrip("]"))

    @classmethod
    def select_all_rows(cls, context) -> bool:
        return select_all_visible_entries(context)


def _entry_icon_kwargs(obj) -> dict:
    if can_be_source(obj):
        return mesh_state_icon_kwargs(obj)
    icon_id = get_mesh_empty_armature_icon_id()
    return {"icon_value": icon_id} if icon_id else {"icon": 'MESH_DATA'}


_FLAG_TOGGLE_OPS = {
    "is_source": "superskin.wt_toggle_source",
    "use_selected_verts": "superskin.wt_toggle_selected_verts",
}


def _draw_flag_toggle(row, index, flag, value, icon):
    op = row.operator(_FLAG_TOGGLE_OPS[flag], text="", icon=icon, depress=value)
    op.index = index



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


_MIN_WIDTH_UNITS = 14
_MAX_WIDTH_UNITS = 32
_ROW_CHROME_UNITS = 9.5


class SUPERSKIN_OT_wt_open_transfer_popover(bpy.types.Operator):
    """Transfer weights between meshes"""
    bl_idname = "superskin.wt_open_transfer_popover"
    bl_label = "Transfer"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return not context.mode.startswith('EDIT')

    def invoke(self, context, event):
        names = [obj.name for obj in context.scene.objects if obj.type == 'MESH']
        units = fit_width_units(
            context, names, _ROW_CHROME_UNITS, _MIN_WIDTH_UNITS, _MAX_WIDTH_UNITS,
        )
        open_popover_at_width(SUPERSKIN_PT_weight_transfer_options, units)
        return {'FINISHED'}


def _draw_options(layout, context, show_force_init_layer=True):
    prefs = context.window_manager.superskin_weight_transfer_prefs
    col = layout.column(align=True)
    row = col.split(factor=0.4, align=True)
    row.label(text="Method:")
    row.prop(prefs, "transfer_method", text="")
    if show_force_init_layer:
        col.prop(prefs, "force_init_layer")


class SUPERSKIN_PT_weight_import(bpy.types.Panel):
    """Import weights from a JSON file"""
    bl_idname = "SUPERSKIN_PT_weight_import"
    bl_label = "Import"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 14

    def draw(self, context):
        layout = self.layout
        _draw_options(layout, context, show_force_init_layer=False)
        layout.separator(factor=0.5)
        layout.operator("superskin.import_weight_json", text="Import")



def _draw_entries(layout, context):
    sync_active_entry_from_viewport(context)
    state = context.scene.superskin_weight_transfer_state

    row = layout.row()
    row.template_list(
        "SUPERSKIN_UL_wt_entries", "", state, "entries", state, "active_entry_index", rows=8,
    )
    col = row.column(align=True)
    col.operator("superskin.wt_add_entry", text="", icon='ADD')
    col.operator("superskin.wt_remove_entry", text="", icon='REMOVE')
    col.separator()
    trash_icon = get_trash_icon_id()
    if trash_icon:
        col.operator("superskin.wt_clear_entries", text="", icon_value=trash_icon)
    else:
        col.operator("superskin.wt_clear_entries", text="", icon='TRASH')



def draw_import_export_section(layout, context):
    grid = layout.grid_flow(
        row_major=True, columns=3, even_columns=True, even_rows=True, align=True,
    )
    grid.scale_y = 1.2
    grid.enabled = not context.mode.startswith('EDIT')

    grid.operator("superskin.wt_open_transfer_popover", text="Transfer...")
    grid.operator("wm.call_panel", text="Import...").name = SUPERSKIN_PT_weight_import.bl_idname
    grid.operator("superskin.export_weight_json", text="Export")



_classes = (
    SUPERSKIN_UL_wt_entries,
    SUPERSKIN_PT_weight_transfer_options,
    SUPERSKIN_PT_weight_import,
    SUPERSKIN_OT_wt_open_transfer_popover,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
