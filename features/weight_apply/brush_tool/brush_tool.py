
import bpy

import os

from ..vertex_tool.common import (
    LASSO_TOOL_IDNAME as _SELECT_LASSO_TOOL_IDNAME,
    NATIVE_KEY_BLOCKS as _NATIVE_KEY_BLOCKS,
    WEIGHT_OPTIONS_PANEL_IDNAME as _WEIGHT_OPTIONS_PANEL_IDNAME,
    poll_session_mesh as _poll_session_mesh,
)

_WEIGHT_BRUSH_TOOL_IDNAME = "superskin.weight_brush_tool"

_keymaps = []


def _get_active_tool_idname(context):
    if context.workspace is None:
        return None
    tool = context.workspace.tools.from_space_view3d_mode('PAINT_WEIGHT', create=False)
    return tool.idname if tool else None


def _resync_active_bone_selection(context) -> None:
    obj = context.active_object
    if not obj or obj.type != 'MESH':
        return
    try:
        from ....interface.template_ui.select_ops import get_adapter
        adapter = get_adapter('BONES')
        _, last_key, _ = adapter.read_selection(context, obj)
        if last_key is not None:
            adapter.on_single_select(context, obj, last_key)
    except Exception:
        pass


class SUPERSKIN_OT_toggle_weight_brush_tool(bpy.types.Operator):
    """Switch between the Weight Brush and Lasso Select tools"""
    bl_idname = "superskin.toggle_weight_brush_tool"
    bl_label = "Toggle Weight Brush Tool"
    bl_options = {'REGISTER'}

    tool: bpy.props.EnumProperty(
        items=[
            ('TOGGLE', "Toggle", "Switch to the tool that is not active"),
            ('BRUSH', "Brush", "Weight Brush"),
            ('VERTEX', "Vertex", "Lasso Select"),
        ],
        default='TOGGLE',
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        return _poll_session_mesh(context)

    @classmethod
    def description(cls, context, properties):
        if properties.tool == 'BRUSH':
            return "Switch the active tool to Weight Brush"
        if properties.tool == 'VERTEX':
            return "Switch the active tool to Lasso Select"
        current = _get_active_tool_idname(context)
        label = "Lasso Select" if current == _WEIGHT_BRUSH_TOOL_IDNAME else "Weight Brush"
        return f"Switch the active tool to {label}"

    def execute(self, context):
        current = _get_active_tool_idname(context)
        if self.tool == 'BRUSH':
            target = _WEIGHT_BRUSH_TOOL_IDNAME
        elif self.tool == 'VERTEX':
            target = _SELECT_LASSO_TOOL_IDNAME
        else:
            target = (
                _SELECT_LASSO_TOOL_IDNAME if current == _WEIGHT_BRUSH_TOOL_IDNAME
                else _WEIGHT_BRUSH_TOOL_IDNAME
            )
        bpy.ops.wm.tool_set_by_id(name=target)
        _resync_active_bone_selection(context)
        from ....core.facade import CoreFacade
        CoreFacade.tag_redraw_areas(window_manager=context.window_manager)
        return {'FINISHED'}


class SUPERSKIN_OT_toggle_brush_projected(bpy.types.Operator):
    """Turn the Weight Brush's Projected option on or off"""
    bl_idname = "superskin.toggle_brush_projected"
    bl_label = "Toggle Projected"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _poll_session_mesh(context)

    def execute(self, context):
        from .brush_ops import get_brush_prefs
        p = get_brush_prefs()
        p.projected = not p.projected
        from ....core.facade import CoreFacade
        CoreFacade.tag_redraw_areas(window_manager=context.window_manager)
        return {'FINISHED'}


_CURSOR_PANEL_IDNAME = "SUPERSKIN_PT_brush_cursor"


class SUPERSKIN_PT_brush_cursor(bpy.types.Panel):
    """Brush cursor color for each projection"""
    bl_idname = _CURSOR_PANEL_IDNAME
    bl_label = "Cursor"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 10

    def draw(self, context):
        from .brush_ops import get_brush_prefs, cursor_color_prop, CURSOR_COLOR_DEFAULTS
        p = get_brush_prefs()
        col = self.layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        for projection, label in (("SURFACE", "Surface"), ("SCREEN", "Projected")):
            prop = cursor_color_prop(projection)
            row = col.row(align=True)
            row.prop(p, prop, text=label)
            if not _colors_match(getattr(p, prop), CURSOR_COLOR_DEFAULTS[projection]):
                row.operator(SUPERSKIN_OT_reset_brush_cursor_color.bl_idname,
                             text="", icon='LOOP_BACK').projection = projection


def _colors_match(a, b):
    return all(abs(x - y) < 1e-4 for x, y in zip(a, b))


class SUPERSKIN_OT_reset_brush_cursor_color(bpy.types.Operator):
    """Reset to the default color"""
    bl_idname = "superskin.reset_brush_cursor_color"
    bl_label = "Reset Cursor Color"
    bl_options = {'INTERNAL'}

    projection: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from .brush_ops import get_brush_prefs, cursor_color_prop, CURSOR_COLOR_DEFAULTS
        if self.projection not in CURSOR_COLOR_DEFAULTS:
            return {'CANCELLED'}
        setattr(get_brush_prefs(), cursor_color_prop(self.projection), CURSOR_COLOR_DEFAULTS[self.projection])
        return {'FINISHED'}


class SuperSkinWeightBrushTool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'PAINT_WEIGHT'
    bl_idname = "superskin.weight_brush_tool"
    bl_label = "Weight Brush"
    bl_description = (
        "Paint weights\nShift: Smooth, Ctrl: Scale, Alt: Sharpen\nF: brush size\nA / Alt+A / Ctrl+I: select all / none / invert\nRight-click: Weight Options"
    )
    bl_icon = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "assets", "toolbar", "tool_brush_menu",
    )
    bl_widget = None
    bl_cursor = 'PAINT_CROSS'
    bl_keymap = (
        ("superskin.weight_brush", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
        ("superskin.weight_brush",
         {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True}, None),
        ("superskin.weight_brush",
         {"type": 'LEFTMOUSE', "value": 'PRESS', "ctrl": True}, None),
        ("superskin.weight_brush",
         {"type": 'LEFTMOUSE', "value": 'PRESS', "alt": True}, None),
        ("superskin.weight_brush",
         {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True, "ctrl": True}, None),
        ("superskin.weight_brush",
         {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True, "alt": True}, None),
        ("superskin.weight_brush",
         {"type": 'LEFTMOUSE', "value": 'PRESS', "ctrl": True, "alt": True}, None),
        ("superskin.weight_brush",
         {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True, "ctrl": True, "alt": True}, None),

        ("superskin.weight_brush_adjust_radius", {"type": 'F', "value": 'PRESS'}, None),
        ("superskin.toggle_brush_projected", {"type": 'F', "value": 'PRESS', "alt": True}, None),
        ("superskin.session_call_panel",
         {"type": 'RIGHTMOUSE', "value": 'PRESS'},
         {"properties": [("name", _WEIGHT_OPTIONS_PANEL_IDNAME), ("keep_open", True)]}),
    ) + tuple(
        ("superskin.block_native_key", {"type": key, "value": 'PRESS', **mods}, None)
        for key, mods in (('A', {}), ('A', {"alt": True}), ('I', {"ctrl": True}), ('H', {}),
                          ('H', {"shift": True}), ('H', {"alt": True}))
    ) + _NATIVE_KEY_BLOCKS

    @staticmethod
    def draw_settings(context, layout, tool):
        from .brush_ops import get_brush_prefs
        from .brush_ui import draw_projection_button, draw_size_row
        p = get_brush_prefs()
        draw_projection_button(layout.row(align=True), p)
        draw_size_row(layout, p)
        layout.prop(p, "falloff", text="")
        layout.popover(panel=_CURSOR_PANEL_IDNAME, text="Cursor")


def register():
    bpy.utils.register_class(SUPERSKIN_OT_toggle_weight_brush_tool)
    bpy.utils.register_class(SUPERSKIN_OT_toggle_brush_projected)
    bpy.utils.register_class(SUPERSKIN_OT_reset_brush_cursor_color)
    bpy.utils.register_class(SUPERSKIN_PT_brush_cursor)

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='Weight Paint', space_type='EMPTY')
        kmi = km.keymap_items.new(
            "superskin.toggle_weight_brush_tool",
            type='RIGHTMOUSE',
            value='PRESS',
            alt=True,
            shift=True,
        )
        _keymaps.append((km, kmi, "Toggle Weight Brush Tool"))


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()

    bpy.utils.unregister_class(SUPERSKIN_PT_brush_cursor)
    bpy.utils.unregister_class(SUPERSKIN_OT_reset_brush_cursor_color)
    bpy.utils.unregister_class(SUPERSKIN_OT_toggle_brush_projected)
    bpy.utils.unregister_class(SUPERSKIN_OT_toggle_weight_brush_tool)


def get_registered_keymap_items():
    return list(_keymaps)
