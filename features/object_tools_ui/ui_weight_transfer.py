"""Weight Transfer / Weight Export / Weight Import — LAYER-tab widget code."""

import bpy

from ..weight_transfer.public_api import sync_active_entry_from_viewport


# ==============================================================================
# UIList — unified Source/Target list
# ==============================================================================

class SUPERSKIN_UL_wt_entries(bpy.types.UIList):
    """One row per SSWeightTransferEntryItem in the unified Source/Target list: the mesh's
    name, an `is_source` radio toggle (RADIOBUT_ON/OFF."""
    bl_idname = "SUPERSKIN_UL_wt_entries"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        if item.object:
            row.label(text=item.object.name, icon='MESH_DATA')
        else:
            row.label(text="(missing mesh)", icon='ERROR')
        source_icon = 'RADIOBUT_ON' if item.is_source else 'RADIOBUT_OFF'
        row.prop(item, "is_source", text="", icon=source_icon, toggle=True)
        toggle_icon = 'VERTEXSEL' if item.use_selected_verts else 'OBJECT_DATA'
        row.prop(item, "use_selected_verts", text="", icon=toggle_icon, toggle=True)


# ==============================================================================
# Options popover — Method / Keep Old Layer Data, shared by Transfer AND
# the combined Import/Export row
# ==============================================================================

class SUPERSKIN_PT_weight_transfer_options(bpy.types.Panel):
    """Popover opened by the "Transfer Weight" button: the transfer operator plus its shared
    options."""
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
        layout.operator("object.mw_copy_skin_weight_maya", text="Transfer Weight")


def _draw_options(layout, context):
    prefs = context.window_manager.superskin_weight_transfer_prefs
    col = layout.column(align=True)
    row = col.split(factor=0.4, align=True)
    row.label(text="Method:")
    row.prop(prefs, "transfer_method", text="")
    col.prop(prefs, "keep_old_layer_data")


class SUPERSKIN_PT_weight_import(bpy.types.Panel):
    """Popover opened by the "Import" button: the import operator plus its shared options."""
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


# ==============================================================================
# Drawing entry points
# ==============================================================================

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


# ==============================================================================
# Import / Export — combined 2-column row
# ==============================================================================
# weight_import (left) / weight_export (right) no longer draw as two separate
# collapsible sections -- per explicit user request (2026-09-15), their
# buttons are combined into one unlabeled row split into two columns, same
# no-chrome pattern as skin_tools_ui's ui_action_grid.py combined grid. Import
# reuses SUPERSKIN_PT_weight_transfer_options (defined above, shared with the
# Transfer button) for its gear-icon settings popover -- Export still has
# nothing to configure (see docs/domains/weight_transfer.md's "No
# configurable export settings"), so it stays a bare button. See
# object_tools_ui_feature.py's _MERGED_ROW_DOMAIN_IDS/_ROW_ANCHOR_DOMAIN_ID.

def draw_import_export_section(layout, context):
    """Entry point for the combined Weight Import / Weight Export row."""
    grid = layout.grid_flow(
        row_major=True, columns=3, even_columns=True, even_rows=True, align=True,
    )
    grid.scale_y = 1.2

    grid.operator("wm.call_panel", text="Transfer...").name = SUPERSKIN_PT_weight_transfer_options.bl_idname
    grid.operator("wm.call_panel", text="Import...").name = SUPERSKIN_PT_weight_import.bl_idname
    grid.operator("superskin.export_weight_json", text="Export")


# ==============================================================================
# Registration (called from features/object_tools_ui/__init__.py)
# ==============================================================================

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
