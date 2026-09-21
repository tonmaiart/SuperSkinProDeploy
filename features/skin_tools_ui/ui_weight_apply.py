"""Weight Apply — SKINNING-tab widget code: the Add/Scale/Smooth/Sharpen rows and the
Brush/Vertex tool-select row."""

import bpy

from ...interface.utils.icons import (
    get_tool_brush_icon_id, get_tool_lasso_icon_id, get_smooth_limit_icon_id,
)
from ...interface.utils.mode_edit_toggle import draw_edit_mask_button
from ..weight_apply.public_api import (
    BRUSH_ENABLED, ACTION_TO_GESTURE_PAIR,
    get_active_weight_tool_idname, WEIGHT_BRUSH_TOOL_IDNAME, LASSO_TOOL_IDNAME,
    WEIGHT_OPTIONS_PANEL_IDNAME,
)


class SUPERSKIN_PT_weight_apply_options(bpy.types.Panel):
    """Right-click popup in the Weight Brush and Weight Select tools."""
    bl_idname = WEIGHT_OPTIONS_PANEL_IDNAME
    bl_label = "Weight Options"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_ui_units_x = 12

    def draw(self, context):
        wm = context.window_manager
        p = wm.superskin_weight_apply_prefs
        layout = self.layout

        col = layout.column(align=True)
        col.prop(p, "add_val", text="Add", slider=True)
        col.prop(p, "scale_val", text="Scale", slider=True)
        col.prop(p, "smooth_val", text="Smooth", slider=True)
        col.prop(p, "sharpen_val", text="Sharpen", slider=True)
        layout.prop(p, "smooth_affected_only", text="Smooth Affected Only")

        layout.separator()
        if get_active_weight_tool_idname(context) == LASSO_TOOL_IDNAME:
            layout.prop(p, "vertex_front_face_only", text="Front Face Only")
        elif BRUSH_ENABLED:
            brush = wm.superskin_weight_brush_prefs
            layout.prop(brush, "brush_projected", text="Projected")
            col = layout.column(align=True)
            col.prop(brush, "brush_hardness", text="Hardness", slider=True)
            col.prop(brush, "brush_radius", text="Size")


def register():
    bpy.utils.register_class(SUPERSKIN_PT_weight_apply_options)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_PT_weight_apply_options)


# =========================================================================
#  Add/Scale/Smooth/Sharpen section (moved from weight_apply/ui.py)
# =========================================================================

def draw_section(layout, context) -> None:
    p = context.window_manager.superskin_weight_apply_prefs

    _draw_tool_row(layout, context)

    col = layout.column(align=True)
    _draw_op_row(col, "add", "Add", p, "add_val")
    col.separator(factor=0.6)
    _draw_op_row(col, "scale", "Scale", p, "scale_val")
    col.separator(factor=0.6)
    _draw_op_row(col, "smooth", "Smooth", p, "smooth_val", toggle_prop="smooth_affected_only")
    col.separator(factor=0.6)
    _draw_op_row(col, "sharpen", "Sharpen", p, "sharpen_val")


def _draw_tool_row(layout, context) -> None:
    """The Brush and Vertex buttons and "Edit Mask" share one row, taller than Blender's
    standard widget size, above the Add/Scale/Smooth/Sharpen rows."""
    tool_row = layout.row()
    tool_row.scale_y = 1.4
    if BRUSH_ENABLED:
        draw_tool_select_buttons(tool_row, context)
    draw_edit_mask_button(
        tool_row, context,
        enter_edit_idname="superskin.enter_layer_edit",
        edit_mask_idname="superskin.toggle_mask_mode",
        edit_mask_text="Mask",
    )


def _draw_op_row(col, action, label, p, val_prop, toggle_prop=None):
    split = col.split(factor=0.25, align=True)
    split.scale_y = 1.2
    split.operator_context = 'EXEC_DEFAULT'
    op = split.operator("superskin.weight_gesture", text=label)
    op.action = ACTION_TO_GESTURE_PAIR[action]
    op.resolved_action = action
    op.intensity = getattr(p, val_prop)
    if toggle_prop is None:
        split.prop(p, val_prop, text="", slider=True)
        return
    tail = split.row(align=True)
    tail.prop(p, val_prop, text="", slider=True)
    icon_id = get_smooth_limit_icon_id()
    icon_kwargs = {"icon_value": icon_id} if icon_id else {"icon": 'MOD_SMOOTH'}
    btn = tail.row(align=True)
    btn.scale_x = 1.2
    btn.prop(p, toggle_prop, text="", toggle=True, **icon_kwargs)


# =========================================================================
#  Brush toolbar row (moved from weight_apply/brush_tool/brush_ui.py)
# =========================================================================

def draw_tool_select_buttons(row, context):
    """Two side-by-side buttons, Brush and Vertex; the active tool's button is pressed."""
    active_idname = get_active_weight_tool_idname(context)
    pair = row.row(align=True)
    pair.scale_x = 1.6
    for tool, icon_id, fallback, idname in (
        ('BRUSH', get_tool_brush_icon_id(), 'BRUSH_DATA', WEIGHT_BRUSH_TOOL_IDNAME),
        ('VERTEX', get_tool_lasso_icon_id(), 'VERTEXSEL', LASSO_TOOL_IDNAME),
    ):
        icon_kwargs = {"icon_value": icon_id} if icon_id else {"icon": fallback}
        op = pair.operator(
            "superskin.toggle_weight_brush_tool", text="",
            depress=(active_idname == idname), **icon_kwargs,
        )
        op.tool = tool
