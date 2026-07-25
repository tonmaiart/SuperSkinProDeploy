"""Weight Brush -- WorkSpaceTool registration.

Replaces the old global Alt+Shift+LMB keymap item (`brush_keymap.py`,
removed) with a proper Toolbar tool, the same way Blender's own Weight
Paint "Draw" brush works: select the tool, then LMB paints while it's
active. Blender's tool system gives the active tool exclusive ownership of
LMB, so unlike a global keymap item this can never collide with another
domain's shortcut -- there is no shortcut to collide.

`SUPERSKIN_OT_weight_brush` itself (`brush_ops.py`) is unchanged -- only
the entry point moves from a global keymap item to this tool's own
`bl_keymap`, which is scoped to "while this tool is the active tool" and
nothing else. Which Weight Apply action a dab performs (Add/Smooth/Scale/
Sharpen) is decided inside the operator from the held modifier key, not
here -- this file only has to make sure every modifier combo of LMB
actually reaches the operator instead of falling through to something
else (see the `bl_keymap` comment below).

F / Shift+F / Ctrl+F adjust Radius / Falloff / Strength interactively via
Blender's own `wm.radial_control` operator -- the same mechanism every
native brush (Sculpt, Vertex/Weight Paint, Grease Pencil Draw) uses for
its own "F to resize brush" convention. No custom modal needed for this.
"""

import bpy


class SuperSkinWeightBrushTool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'EDIT_MESH'
    bl_idname = "superskin.weight_brush_tool"
    bl_label = "Weight Brush"
    bl_description = (
        "Hold to paint weight (SuperSkinPro).\n"
        "Shift: Smooth, Ctrl: Scale, Alt: Sharpen\n"
        "F: Radius, Shift+F: Falloff, Ctrl+F: Strength"
    )
    # Reuses Blender's own Weight Paint "Draw" brush icon rather than
    # shipping custom icon art.
    bl_icon = "brush.paint_weight.draw"
    bl_widget = None
    # A keymap item with a modifier left unset means "that modifier must NOT
    # be held" (Blender keymap matching, not "don't care") -- every modifier
    # combination of LMB must be listed explicitly to actually claim it
    # while this tool is active, or the unlisted combos fall straight
    # through to whatever the underlying Edit Mesh keymap does with them:
    # Shift/Ctrl+Click normally extend/subtract the mesh selection, and
    # Alt+Click is `../ops.py`'s own Weight Apply gesture -- all three would
    # otherwise fight the brush instead of driving it. All eight LMB combos
    # route to the SAME operator; `_resolve_mode()` in brush_ops.py reads
    # the actual modifier state live from the event every tick, so which
    # action a combo performs isn't baked into the keymap at all.
    #
    # F / Shift+F / Ctrl+F: Blender's built-in interactive radial-control
    # gesture (press, move mouse to resize, click/Enter to confirm, Esc/RMB
    # to cancel) pointed at this tool's own prefs -- no custom modal.
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

        ("wm.radial_control", {"type": 'F', "value": 'PRESS'}, {
            "properties": [
                ("data_path_primary", "window_manager.superskin_weight_brush_prefs.brush_radius"),
                ("header_text", "Weight Brush Radius: %.3f"),
            ],
        }),
        ("wm.radial_control", {"type": 'F', "value": 'PRESS', "shift": True}, {
            "properties": [
                ("data_path_primary", "window_manager.superskin_weight_brush_prefs.brush_falloff"),
                ("header_text", "Weight Brush Falloff: %.2f"),
            ],
        }),
        ("wm.radial_control", {"type": 'F', "value": 'PRESS', "ctrl": True}, {
            "properties": [
                ("data_path_primary", "window_manager.superskin_weight_brush_prefs.brush_strength"),
                ("header_text", "Weight Brush Strength: %.2f"),
            ],
        }),
    )

    @staticmethod
    def draw_settings(context, layout, tool):
        """Radius/Falloff/Strength in the 3D-viewport header while this
        tool is active -- mirrors Weight Paint's own brush header row.
        Reads/writes the SAME `SSPrefWeightBrush` prefs as the N-panel row
        (`brush_ui.py::draw_brush_row()`), so the two stay in sync with no
        extra state to manage. No Mode field -- mode is read live from the
        held modifier key (see `bl_description` above), not stored."""
        from .brush_ops import get_brush_prefs
        p = get_brush_prefs()
        layout.prop(p, "brush_projection", text="")
        layout.prop(p, "brush_radius", text="Radius")
        layout.prop(p, "brush_falloff", text="Falloff")
        layout.prop(p, "brush_strength", text="Strength")


def register():
    # Placed right after Blender's default Move/Rotate/Scale/Transform
    # group, with a separator -- no strong reason to place it elsewhere.
    bpy.utils.register_tool(
        SuperSkinWeightBrushTool, after={"builtin.transform"}, separator=True,
    )


def unregister():
    bpy.utils.unregister_tool(SuperSkinWeightBrushTool)
