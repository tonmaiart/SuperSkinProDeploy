
import bpy

import os

from ..vertex_tool.common import (
    LASSO_TOOL_IDNAME as _SELECT_LASSO_TOOL_IDNAME,
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


class SUPERSKIN_OT_toggle_brush_projection(bpy.types.Operator):
    """Switch the Weight Brush between Surface and Projected"""
    bl_idname = "superskin.toggle_brush_projection"
    bl_label = "Toggle Brush Projection"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _poll_session_mesh(context)

    def execute(self, context):
        from .brush_ops import get_brush_prefs
        p = get_brush_prefs()
        p.brush_projection = 'SURFACE' if p.brush_projection == 'SCREEN' else 'SCREEN'
        from ....core.facade import CoreFacade
        CoreFacade.tag_redraw_areas(window_manager=context.window_manager)
        return {'FINISHED'}


class SuperSkinWeightBrushTool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'PAINT_WEIGHT'
    bl_idname = "superskin.weight_brush_tool"
    bl_label = "Weight Brush"
    bl_description = (
        "Paint weights\nShift: Smooth, Ctrl: Scale, Alt: Sharpen\nF: brush size, Shift+F: brush hardness\nA / Alt+A / Ctrl+I: select all / none / invert\nRight-click: Weight Options"
    )
    bl_icon = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "assets", "tool_brush",
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
        ("superskin.weight_brush_adjust_hardness",
         {"type": 'F', "value": 'PRESS', "shift": True}, None),
        ("superskin.toggle_brush_projection",
         {"type": 'F', "value": 'PRESS', "alt": True}, None),
        ("wm.call_panel",
         {"type": 'RIGHTMOUSE', "value": 'PRESS'},
         {"properties": [("name", _WEIGHT_OPTIONS_PANEL_IDNAME), ("keep_open", False)]}),

        ("superskin.weight_select_all", {"type": 'A', "value": 'PRESS'}, None),
        ("superskin.weight_select_none", {"type": 'A', "value": 'PRESS', "alt": True}, None),
        ("superskin.weight_select_invert", {"type": 'I', "value": 'PRESS', "ctrl": True}, None),
        ("superskin.grow_selection", {"type": 'NUMPAD_PLUS', "value": 'PRESS', "ctrl": True}, None),
        ("superskin.shrink_selection", {"type": 'NUMPAD_MINUS', "value": 'PRESS', "ctrl": True}, None),
    )

    @staticmethod
    def draw_settings(context, layout, tool):
        from .brush_ops import get_brush_prefs
        from .brush_ui import draw_projection_button, draw_hardness_slider
        p = get_brush_prefs()
        draw_projection_button(layout.row(align=True), p)
        row = layout.row(align=True)
        draw_hardness_slider(row, p)
        row.prop(p, "brush_radius", text="Size")


def register():
    bpy.utils.register_class(SUPERSKIN_OT_toggle_weight_brush_tool)
    bpy.utils.register_class(SUPERSKIN_OT_toggle_brush_projection)

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

    bpy.utils.unregister_class(SUPERSKIN_OT_toggle_brush_projection)
    bpy.utils.unregister_class(SUPERSKIN_OT_toggle_weight_brush_tool)


def get_registered_keymap_items():
    return list(_keymaps)
