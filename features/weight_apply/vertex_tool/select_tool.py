"""Weight Select -- this addon's own WorkSpaceTool for vertex selection."""

import os

import bpy

from .common import LASSO_TOOL_IDNAME, WEIGHT_OPTIONS_PANEL_IDNAME

_ICON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "assets", "tool_lasso",
)


class SuperSkinWeightSelectTool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'PAINT_WEIGHT'
    bl_idname = LASSO_TOOL_IDNAME
    bl_label = "Weight Select"
    bl_description = (
        "Lasso select vertices (Shift: add, Ctrl: subtract). Click: select, Ctrl+Click: shortest path from the last clicked vertex, double click: select linked.\n"
        "A / Alt+A / Ctrl+I: all / none / invert, Ctrl+Numpad +/-: grow / shrink.\n"
        "Shift+Alt+LMB: add edge loop to the selection.\n"
        "Alt+LMB: Add / Scale gesture, Alt+RMB: Smooth / Sharpen gesture"
    )
    bl_icon = _ICON_PATH
    bl_widget = None
    bl_keymap = (
        ("superskin.weight_select_lasso",
         {"type": 'LEFTMOUSE', "value": 'CLICK_DRAG'}, {"properties": [("mode", 'SET')]}),
        ("superskin.weight_select_lasso",
         {"type": 'LEFTMOUSE', "value": 'CLICK_DRAG', "shift": True},
         {"properties": [("mode", 'ADD')]}),
        ("superskin.weight_select_lasso",
         {"type": 'LEFTMOUSE', "value": 'CLICK_DRAG', "ctrl": True},
         {"properties": [("mode", 'SUB')]}),
        ("superskin.weight_select_lasso",
         {"type": 'LEFTMOUSE', "value": 'CLICK_DRAG', "shift": True, "ctrl": True},
         {"properties": [("mode", 'AND')]}),
        ("superskin.weight_select_click",
         {"type": 'LEFTMOUSE', "value": 'CLICK'}, {"properties": [("toggle", False)]}),
        ("superskin.weight_select_click",
         {"type": 'LEFTMOUSE', "value": 'CLICK', "shift": True},
         {"properties": [("toggle", True)]}),
        ("superskin.weight_select_click",
         {"type": 'LEFTMOUSE', "value": 'CLICK', "ctrl": True}, {"properties": [("toggle", False)]}),
        ("superskin.weight_select_linked",
         {"type": 'LEFTMOUSE', "value": 'DOUBLE_CLICK'}, {"properties": [("extend", False)]}),
        ("superskin.weight_select_linked",
         {"type": 'LEFTMOUSE', "value": 'DOUBLE_CLICK', "shift": True},
         {"properties": [("extend", True)]}),
        ("superskin.weight_select_linked_selected", {"type": 'L', "value": 'PRESS', "ctrl": True}, None),
        ("superskin.weight_select_all", {"type": 'A', "value": 'PRESS'}, None),
        ("superskin.weight_select_none", {"type": 'A', "value": 'PRESS', "alt": True}, None),
        ("superskin.weight_select_invert", {"type": 'I', "value": 'PRESS', "ctrl": True}, None),
        ("superskin.grow_selection", {"type": 'NUMPAD_PLUS', "value": 'PRESS', "ctrl": True}, None),
        ("superskin.shrink_selection", {"type": 'NUMPAD_MINUS', "value": 'PRESS', "ctrl": True}, None),
        ("superskin.weight_select_loop",
         {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True, "alt": True},
         {"properties": [("mode", 'ADD')]}),
        ("superskin.weight_gesture",
         {"type": 'LEFTMOUSE', "value": 'PRESS', "alt": True},
         {"properties": [("action", 'add_scale')]}),
        ("superskin.weight_gesture",
         {"type": 'LEFTMOUSE', "value": 'PRESS', "alt": True, "ctrl": True},
         {"properties": [("action", 'add_scale')]}),
        ("superskin.weight_gesture",
         {"type": 'RIGHTMOUSE', "value": 'PRESS', "alt": True},
         {"properties": [("action", 'smooth_sharpen')]}),
        ("superskin.weight_gesture",
         {"type": 'RIGHTMOUSE', "value": 'PRESS', "alt": True, "ctrl": True},
         {"properties": [("action", 'smooth_sharpen')]}),
        ("wm.call_panel",
         {"type": 'RIGHTMOUSE', "value": 'PRESS'},
         {"properties": [("name", WEIGHT_OPTIONS_PANEL_IDNAME), ("keep_open", False)]}),
    )

    @staticmethod
    def draw_settings(context, layout, tool):
        prefs = context.window_manager.superskin_weight_apply_prefs
        layout.prop(prefs, "vertex_front_face_only", text="Front Face Only")


def register():
    bpy.utils.register_tool(SuperSkinWeightSelectTool, after={"builtin.transform"}, separator=True)


def unregister():
    bpy.utils.unregister_tool(SuperSkinWeightSelectTool)
