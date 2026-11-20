
import bpy

from ..weight_apply.public_api import (
    WEIGHT_BRUSH_TOOL_IDNAME, get_active_weight_tool_idname, has_vertex_selection,
)

_ROW_SCALE = 1.2
_COLUMN_GAP = 0.25


def _selection_tools(context):
    return get_active_weight_tool_idname(context) != WEIGHT_BRUSH_TOOL_IDNAME


def _has_selection(context):
    obj = context.active_object
    return obj is not None and obj.type == 'MESH' and has_vertex_selection(obj.data)


def _popover(layout, text, panel):
    layout.operator("superskin.open_toolkit_popover", text=text).panel = panel


class SUPERSKIN_PT_action_grid_more_options(bpy.types.Panel):
    """More options"""
    bl_idname = "SUPERSKIN_PT_action_grid_more_options"
    bl_label = "More"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 8

    def draw(self, context):
        col = self.layout.column()
        col.scale_y = _ROW_SCALE
        _popover(col, "Limit Total...", "SUPERSKIN_PT_limit_total_options")
        col.operator("mesh.ssp_normalize_layer", text="Normalize")


def draw_section(layout, context) -> None:
    selection_tools = _selection_tools(context)

    def needs_selection(draw):
        def cell_fn(cell):
            cell.enabled = has_selection
            draw(cell)
        return cell_fn

    has_selection = selection_tools and _has_selection(context)

    def block(cell):
        cell.enabled = not getattr(context.scene, "superskin_is_mask_mode", False)
        cell.operator("mesh.auto_assign_closest_unlocked_bone", text="Block Weight")

    cells = [
        block,
        lambda cell: cell.operator("superskin.open_mirror_options", text="Mirror..."),
        lambda cell: cell.operator("superskin.open_copy_weight_options", text="Copy Weight..."),
        needs_selection(lambda cell: _popover(cell, "Copy Vertex...", "SUPERSKIN_PT_copy_vertex_options")),
        needs_selection(lambda cell: cell.operator("superskin.open_hammer_popup", text="Hammer...")),
        needs_selection(lambda cell: _popover(cell, "Self Transfer...", "SUPERSKIN_PT_self_transfer_options")),
        lambda cell: _popover(cell, "Select...", "SUPERSKIN_PT_select_vertices_options"),
        lambda cell: _popover(cell, "More...", SUPERSKIN_PT_action_grid_more_options.bl_idname),
    ]
    if not selection_tools:
        del cells[3:7]

    col = layout.column()
    col.scale_y = _ROW_SCALE
    for left, right in zip(cells[::2], cells[1::2]):
        row = col.row(align=True)
        left(row.row(align=True))
        row.separator(factor=_COLUMN_GAP)
        right(row.row(align=True))


def draw_brush_toggles(layout, context) -> None:
    brush_prefs = getattr(context.window_manager, "superskin_weight_brush_prefs", None)
    if brush_prefs is not None:
        row = layout.row()
        row.prop(brush_prefs, "keep_volume", text="Keep Volume")
        row.prop(brush_prefs, "mirror_x", text="Auto Mirror")


def register():
    bpy.utils.register_class(SUPERSKIN_PT_action_grid_more_options)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_PT_action_grid_more_options)
