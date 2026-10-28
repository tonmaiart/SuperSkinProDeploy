
import bpy

from ...interface.utils.icons import (
    get_smooth_limit_icon_id, get_fill_add_icon_id, get_fill_scale_icon_id,
)
from ..weight_apply.public_api import (
    BRUSH_ENABLED, ACTION_TO_GESTURE_PAIR,
    get_active_weight_tool_idname, LASSO_TOOL_IDNAME,
    WEIGHT_OPTIONS_PANEL_IDNAME, AMOUNT_SKIP_OP,
    draw_brush_mode, draw_connected_only_icon, draw_falloff_buttons,
)


_OPTIONS_UNITS_X = 13
_SIDEBAR_CHROME_UNITS = 2.0
_POPOVER_ROW_SCALE = 1.2


class SUPERSKIN_PT_weight_apply_options(bpy.types.Panel):
    """Weight and brush settings"""
    bl_idname = WEIGHT_OPTIONS_PANEL_IDNAME
    bl_label = "Weight Options"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_ui_units_x = _OPTIONS_UNITS_X

    @classmethod
    def fit_units(cls, context):
        area = context.area
        sidebar = next((r for r in area.regions if r.type == 'UI'), None) if area else None
        if sidebar is None or sidebar.width <= 1:
            return cls.bl_ui_units_x
        scale = context.preferences.system.ui_scale or 1.0
        units = sidebar.width / (20.0 * scale) - _SIDEBAR_CHROME_UNITS
        return int(min(max(units + 0.5, 8), 40))

    def draw(self, context):
        wm = context.window_manager
        p = wm.superskin_weight_apply_prefs
        layout = self.layout

        is_brush = BRUSH_ENABLED and get_active_weight_tool_idname(context) != LASSO_TOOL_IDNAME
        if is_brush:
            top = layout.row()
            top.scale_y = _POPOVER_ROW_SCALE
            brush = wm.superskin_weight_brush_prefs
            draw_brush_mode(top.row(align=True), brush)
            draw_connected_only_icon(top, brush, _ICON_SCALE_X)
            falloff = top.row(align=True)
            falloff.scale_x = _ICON_SCALE_X
            draw_falloff_buttons(falloff, brush)
        draw_section(layout, context)

        if not is_brush:
            layout.separator()
            layout.prop(p, "vertex_front_face_only", text="Front Face Only for Lasso")


def register():
    bpy.utils.register_class(SUPERSKIN_PT_weight_apply_options)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_PT_weight_apply_options)



def draw_section(layout, context) -> None:
    p = context.window_manager.superskin_weight_apply_prefs
    col = layout.column(align=True)
    _draw_op_row(col, "add", "Add", p, "add_val", fill_op="superskin.flood_full_weight")
    col.separator(factor=0.6)
    _draw_op_row(col, "scale", "Scale", p, "scale_val", fill_op="superskin.flood_zero_weight")
    col.separator(factor=0.6)
    _draw_op_row(col, "smooth", "Smooth", p, "smooth_val", toggle_prop="smooth_affected_only")
    col.separator(factor=0.6)
    _draw_op_row(col, "sharpen", "Sharpen", p, "sharpen_val")


_BUTTON_SHARE = 0.25
_SECTION_ROW_SCALE = 1.2
_ICON_SCALE_X = 1.2
_ROW_GAP = 0.5


def _draw_amount(layout, p, action, label=""):
    row = layout.row(align=True)
    row.operator_context = 'EXEC_DEFAULT'
    row.prop(p, f"{action}_val", text=label, slider=True)
    for icon, direction in (('TRIA_LEFT', -1), ('TRIA_RIGHT', 1)):
        op = row.operator(AMOUNT_SKIP_OP, text="", icon=icon)
        op.action, op.direction = action, direction


_FILL_ICON_GETTERS = {"add": get_fill_add_icon_id, "scale": get_fill_scale_icon_id}


def _draw_op_row(col, action, label, p, val_prop, toggle_prop=None, fill_op=None):
    row = col.row(align=True)
    row.scale_y = _SECTION_ROW_SCALE
    row.operator_context = 'EXEC_DEFAULT'
    head = row.row(align=True)
    head.ui_units_x = 10 * _BUTTON_SHARE
    op = head.operator("superskin.weight_gesture", text=label)
    op.action = ACTION_TO_GESTURE_PAIR[action]
    op.resolved_action = action
    op.intensity = getattr(p, val_prop)
    tail = row.row(align=True)
    tail.ui_units_x = 10 * (1 - _BUTTON_SHARE)
    tail.separator(factor=_ROW_GAP)
    _draw_amount(tail, p, action)
    tail.separator(factor=_ROW_GAP)
    _draw_icon_button(tail, p, action, toggle_prop, fill_op)


def _draw_icon_button(row, p, action, toggle_prop, fill_op):
    btn = row.row(align=True)
    btn.scale_x = _ICON_SCALE_X
    if toggle_prop is not None:
        icon_id = get_smooth_limit_icon_id()
        icon_kwargs = {"icon_value": icon_id} if icon_id else {"icon": 'MOD_SMOOTH'}
        btn.prop(p, toggle_prop, text="", toggle=True, **icon_kwargs)
    elif fill_op is not None:
        icon_id = _FILL_ICON_GETTERS[action]()
        icon_kwargs = {"icon_value": icon_id} if icon_id else {"icon": 'IPO_CONSTANT'}
        btn.operator(fill_op, text="", **icon_kwargs)
    else:
        btn.label(text="", icon='BLANK1')
