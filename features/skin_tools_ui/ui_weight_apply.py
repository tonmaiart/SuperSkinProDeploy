
import bpy

from ...interface.utils.icons import (
    get_smooth_limit_icon_id, get_fill_add_icon_id, get_fill_scale_icon_id,
)
from ..weight_apply.public_api import (
    BRUSH_ENABLED, ACTION_TO_GESTURE_PAIR,
    get_active_weight_tool_idname, LASSO_TOOL_IDNAME,
    WEIGHT_OPTIONS_PANEL_IDNAME,
)


class SUPERSKIN_PT_weight_apply_options(bpy.types.Panel):
    """Weight and brush settings"""
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
            layout.row(align=True).prop(brush, "brush_projection", expand=True)
            col = layout.column(align=True)
            col.prop(brush, "brush_hardness", text="Hardness", slider=True)
            col.prop(brush, "brush_radius", text="Size")


def register():
    bpy.utils.register_class(SUPERSKIN_PT_weight_apply_options)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_PT_weight_apply_options)



def draw_section(layout, context) -> None:
    p = context.window_manager.superskin_weight_apply_prefs

    col = layout.column(align=True)
    _draw_op_row(col, "add", "Add", p, "add_val", fill_intensity=1.0)
    col.separator(factor=0.6)
    _draw_op_row(col, "scale", "Scale", p, "scale_val", fill_intensity=0.0)
    col.separator(factor=0.6)
    _draw_op_row(col, "smooth", "Smooth", p, "smooth_val", toggle_prop="smooth_affected_only")
    col.separator(factor=0.6)
    _draw_op_row(col, "sharpen", "Sharpen", p, "sharpen_val")


_FILL_ICON_GETTERS = {"add": get_fill_add_icon_id, "scale": get_fill_scale_icon_id}


def _draw_op_row(col, action, label, p, val_prop, toggle_prop=None, fill_intensity=None):
    split = col.split(factor=0.25, align=True)
    split.scale_y = 1.2
    split.operator_context = 'EXEC_DEFAULT'
    op = split.operator("superskin.weight_gesture", text=label)
    op.action = ACTION_TO_GESTURE_PAIR[action]
    op.resolved_action = action
    op.intensity = getattr(p, val_prop)
    if toggle_prop is not None:
        tail = split.row(align=True)
        tail.prop(p, val_prop, text="", slider=True)
        icon_id = get_smooth_limit_icon_id()
        icon_kwargs = {"icon_value": icon_id} if icon_id else {"icon": 'MOD_SMOOTH'}
        btn = tail.row(align=True)
        btn.scale_x = 1.2
        btn.prop(p, toggle_prop, text="", toggle=True, **icon_kwargs)
        return
    if fill_intensity is not None:
        tail = split.row(align=True)
        tail.prop(p, val_prop, text="", slider=True)
        get_icon_id = _FILL_ICON_GETTERS[action]
        icon_id = get_icon_id()
        icon_kwargs = {"icon_value": icon_id} if icon_id else {"icon": 'IPO_CONSTANT'}
        btn = tail.row(align=True)
        btn.scale_x = 1.2
        fill_op = btn.operator("superskin.weight_gesture", text="", **icon_kwargs)
        fill_op.action = ACTION_TO_GESTURE_PAIR[action]
        fill_op.resolved_action = action
        fill_op.intensity = fill_intensity
        return
    split.prop(p, val_prop, text="", slider=True)
