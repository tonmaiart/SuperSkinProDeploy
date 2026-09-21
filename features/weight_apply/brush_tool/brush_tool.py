"""Weight Brush -- WorkSpaceTool registration."""

import bpy

import os

from ..vertex_tool.common import (
    LASSO_TOOL_IDNAME as _SELECT_LASSO_TOOL_IDNAME,
    WEIGHT_OPTIONS_PANEL_IDNAME as _WEIGHT_OPTIONS_PANEL_IDNAME,
)

_WEIGHT_BRUSH_TOOL_IDNAME = "superskin.weight_brush_tool"

_keymaps = []


def _get_active_tool_idname(context):
    """Return the active VIEW_3D/PAINT_WEIGHT tool's idname, or None."""
    if context.workspace is None:
        return None
    tool = context.workspace.tools.from_space_view3d_mode('PAINT_WEIGHT', create=False)
    return tool.idname if tool else None


class SUPERSKIN_OT_toggle_weight_brush_tool(bpy.types.Operator):
    """Alt+Shift+Right Click: toggle the active tool between Weight Brush (this file's tool)
    and Lasso Select."""
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
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return {'FINISHED'}


class SuperSkinWeightBrushTool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'PAINT_WEIGHT'
    bl_idname = "superskin.weight_brush_tool"
    bl_label = "Weight Brush"
    bl_description = (
        "Hold to paint weight (SuperSkinPro).\n"
        "Shift: Smooth, Ctrl: Scale, Alt: Sharpen\n"
        "F: Radius, Shift+F: Hardness, Alt+F: Surface/Screen Projection\n"
        "A / Alt+A / Ctrl+I: select all / none / invert, Ctrl+Numpad +/-: grow / shrink\n"
        "Intensity uses the Add/Scale/Smooth/Sharpen sliders; Hardness only "
        "affects the falloff toward the brush edge"
    )
    bl_icon = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "assets", "tool_brush",
    )
    bl_widget = None
    # Lets Blender's OWN engine-level tool-cursor system swap the OS cursor
    # to a crosshair while hovering this tool's applicable region, and back
    # to the default automatically over the N-panel, Toolbar, header, the
    # viewport navigation gizmo, and everywhere else -- correctly respecting
    # region/gizmo boundaries in a way a hand-rolled `cursor_modal_set('NONE')`/
    # `cursor_modal_restore()` never reliably could (see
    # docs/bug-history/0038). Confirmed NOT responsible for that bug's
    # "N-panel unclickable" symptom (temporarily disabled during diagnosis,
    # bug persisted; the real cause was the MOUSEMOVE entry that used to be
    # in `bl_keymap` below, now moved to `keymap.py`) -- safe to keep.
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
        ("superskin.cycle_brush_projection",
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
        """Projection/Hardness/Radius in the 3D-viewport header while this tool is active."""
        from .brush_ops import get_brush_prefs
        from .brush_ui import draw_projection_button, draw_hardness_slider
        p = get_brush_prefs()
        row = layout.row(align=True)
        draw_projection_button(row, p)
        draw_hardness_slider(row, p)
        row.prop(p, "brush_radius", text="Size")


def register():
    bpy.utils.register_class(SUPERSKIN_OT_toggle_weight_brush_tool)
    # Placed right after Blender's default Move/Rotate/Scale/Transform
    # group, with a separator -- no strong reason to place it elsewhere.
    bpy.utils.register_tool(
        SuperSkinWeightBrushTool, after={"builtin.transform"}, separator=True,
    )

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

    bpy.utils.unregister_tool(SuperSkinWeightBrushTool)
    bpy.utils.unregister_class(SUPERSKIN_OT_toggle_weight_brush_tool)


def get_registered_keymap_items():
    """Return the ``(km, kmi, label)`` triples registered on the addon keyconfig by
    ``register()`` above, read-only."""
    return list(_keymaps)
