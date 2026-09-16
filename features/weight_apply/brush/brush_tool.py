"""Weight Brush -- WorkSpaceTool registration.

Gives the "Weight Brush" its own Toolbar entry, the same way Blender's own
Weight Paint "Draw" brush works: select the tool, then LMB paints while it's
active. Blender's tool system gives the active tool exclusive ownership of
LMB, so unlike a global keymap item this can never collide with another
domain's shortcut -- there is no shortcut to collide.

`SUPERSKIN_OT_weight_brush` itself (`brush_ops.py`) is unchanged -- only
the entry point is this tool's own `bl_keymap`, which is scoped to "while
this tool is the active tool" and nothing else. Which Weight Apply action a
dab performs (Add/Smooth/Scale/Sharpen) is decided inside the operator from
the held modifier key, not here -- this file only has to make sure every
modifier combo of LMB actually reaches the operator instead of falling
through to something else (see the `bl_keymap` comment below).

The hover-preview circle's own operator (`superskin.weight_brush_hover`,
`brush_hover.py`) is deliberately NOT bound here -- see `keymap.py` in this
same package and `docs/bug-history/0038` for why a bare `MOUSEMOVE` entry
inside THIS tool's own `bl_keymap` turned out to be actively harmful.

F (Radius) uses a custom modal (`brush_radius_adjust.py`) instead of
Blender's native `wm.radial_control` -- `wm.radial_control`'s generic
preview circle assumes the controlled property is a literal on-screen
pixel radius, which `brush_radius` isn't (world-space mesh units in
Surface projection, a fraction of a fixed pixel scale in Screen), so its
preview came out far smaller than the actual brush cursor; see that
module's docstring. Hardness has no interactive key binding at all -- it's
a 3-preset `EnumProperty` (`brush_ops.py`'s `SSPrefWeightBrush.
brush_hardness`; Hard/Medium/Soft), picked via a single cycle button
(`SUPERSKIN_OT_cycle_brush_hardness`, `brush_ops.py`) from the N-panel row
or this tool's own viewport header (`draw_settings()` below), not dragged.

No independent Strength/intensity control (and no Ctrl+F) -- removed per
explicit request: a dab's intensity always tracks whichever of the
Add/Scale/Smooth/Sharpen N-panel sliders corresponds to the currently-held
modifier (`brush_ops.py::_slider_intensity()`), the same value a plain
panel-button click or the Alt-drag gesture would use, rather than a
separate brush-only value that could drift out of sync with them.
"""

import bpy

from ...lasso_tool_adjust.public_api import LASSO_TOOL_IDNAME as _SELECT_LASSO_TOOL_IDNAME

_WEIGHT_BRUSH_TOOL_IDNAME = "superskin.weight_brush_tool"
# The other tool `SUPERSKIN_OT_toggle_weight_brush_tool` below cycles to --
# both its Alt+1 keymap AND the N-panel's single Brush/Lasso cycle button
# (`brush_ui.py::draw_tool_select_buttons()`) share this ONE operator (see
# that operator's own docstring). Imported from `lasso_tool_adjust`'s
# `public_api.py` rather than hardcoded here, per this project's
# "Controlled Cross-Domain Imports" rule -- see
# `docs/domains/lasso_tool_adjust.md`. The N-panel button used to be two
# separate buttons (originally Brush/Box/Circle, then Brush/Lasso after
# `circle_tool_adjust` was removed) before being merged into this single
# cycle button -- both changes per explicit user request.

_keymaps = []


def _get_active_tool_idname(context):
    """Return the active VIEW_3D/EDIT_MESH tool's idname, or None."""
    if context.workspace is None:
        return None
    tool = context.workspace.tools.from_space_view3d_mode('EDIT_MESH', create=False)
    return tool.idname if tool else None


class SUPERSKIN_OT_toggle_weight_brush_tool(bpy.types.Operator):
    """Alt+1 (also the N-panel's single Brush/Lasso cycle button, per
    explicit user request replacing what used to be two separate buttons --
    see `brush_ui.py::draw_tool_select_buttons()`): toggle the active tool
    between Weight Brush (this file's tool) and Lasso Select. Pressing it
    again while Weight Brush is already active switches back to Lasso
    Select -- rather than tracking whatever tool happened to be active
    before, so the shortcut/button reads as one simple paint/select pair.
    Originally bound to Alt+Shift+MMB and paired with Select Circle
    (`circle_tool_adjust`'s own Alt+Shift+RMB gesture toggled/adjusted the
    other side of that pair); retargeted to Lasso Select when that domain
    was removed entirely, then rebound to Alt+1 -- both per explicit user
    request."""
    bl_idname = "superskin.toggle_weight_brush_tool"
    bl_label = "Toggle Weight Brush Tool"
    bl_options = {'REGISTER'}

    @classmethod
    def description(cls, context, properties):
        current = _get_active_tool_idname(context)
        label = "Lasso Select" if current == _WEIGHT_BRUSH_TOOL_IDNAME else "Weight Brush"
        return f"Switch the active tool to {label}"

    def execute(self, context):
        current = _get_active_tool_idname(context)
        target = (
            _SELECT_LASSO_TOOL_IDNAME if current == _WEIGHT_BRUSH_TOOL_IDNAME
            else _WEIGHT_BRUSH_TOOL_IDNAME
        )
        bpy.ops.wm.tool_set_by_id(name=target)
        return {'FINISHED'}


class SuperSkinWeightBrushTool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'EDIT_MESH'
    bl_idname = "superskin.weight_brush_tool"
    bl_label = "Weight Brush"
    bl_description = (
        "Hold to paint weight (SuperSkinPro).\n"
        "Shift: Smooth, Ctrl: Scale, Alt: Sharpen\n"
        "F: Radius, Hardness: pick Hard/Medium/Soft below\n"
        "Intensity uses the Add/Scale/Smooth/Sharpen sliders"
    )
    # Reuses Blender's own Weight Paint "Draw" brush icon rather than
    # shipping custom icon art.
    bl_icon = "brush.paint_weight.draw"
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
    # F: custom radius-adjust modal (brush_radius_adjust.py) -- see the
    # module docstring above for why Radius needed a custom modal instead
    # of Blender's native `wm.radial_control`. No Shift+F entry -- Hardness
    # is a 3-preset EnumProperty, picked from a single cycle button, not
    # dragged. No Ctrl+F/Strength entry -- see the module docstring's "No
    # independent Strength/intensity control".
    bl_keymap = (
        # The hover-preview circle (brush_hover.py) is NOT bound here --
        # binding a bare MOUSEMOVE/ANY entry inside a WorkSpaceTool's own
        # bl_keymap turned out to silently break ALL N-panel/Toolbar
        # interaction in the 3D View editor for as long as this tool stayed
        # selected (confirmed by direct experiment; see
        # docs/bug-history/0038). It's registered instead as a plain
        # 'Mesh'-mode addon keymap in `keymap.py`, the same convention every
        # other custom keymap in this codebase already uses.
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
    )

    @staticmethod
    def draw_settings(context, layout, tool):
        """Projection/Hardness/Radius in the 3D-viewport header while this
        tool is active -- mirrors Weight Paint's own brush header row.
        Reads/writes the SAME `SSPrefWeightBrush` prefs as the N-panel row
        (`brush_ui.py::_draw_brush_settings()`), so the two stay in sync with no
        extra state to manage. No Mode field -- mode is read live from the
        held modifier key (see `bl_description` above), not stored. No
        Strength field -- intensity comes from the Add/Scale/Smooth/Sharpen
        sliders instead (see the module docstring). Same order and same
        uniformly-sized custom Projection cycle button / Hardness cycle
        button AND the same Radius height-matching as the N-panel row --
        see `brush_ui.py::draw_icon_button_row()`/`_ROW_SCALE_Y`."""
        from .brush_ops import get_brush_prefs
        from .brush_ui import draw_icon_button_row, _ROW_SCALE_Y
        p = get_brush_prefs()
        row = layout.row(align=True)
        row.scale_y = _ROW_SCALE_Y
        draw_icon_button_row(row, p)
        row.prop(p, "brush_radius", text="Radius")


def register():
    bpy.utils.register_class(SUPERSKIN_OT_toggle_weight_brush_tool)
    # Placed right after Blender's default Move/Rotate/Scale/Transform
    # group, with a separator -- no strong reason to place it elsewhere.
    bpy.utils.register_tool(
        SuperSkinWeightBrushTool, after={"builtin.transform"}, separator=True,
    )

    # Global entry point INTO the tool (and back out again) -- deliberately
    # a plain 'Mesh'-mode addon keymap, not an entry in `bl_keymap` above:
    # that dict only applies while this tool is ALREADY active, so it can
    # never be the thing that switches you into it in the first place.
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
        kmi = km.keymap_items.new(
            "superskin.toggle_weight_brush_tool",
            type='ONE',
            value='PRESS',
            alt=True,
        )
        _keymaps.append((km, kmi, "Toggle Weight Brush Tool"))


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()

    bpy.utils.unregister_tool(SuperSkinWeightBrushTool)
    bpy.utils.unregister_class(SUPERSKIN_OT_toggle_weight_brush_tool)


def get_registered_keymap_items():
    """Return the ``(km, kmi, label)`` triples registered on the addon
    keyconfig by ``register()`` above, read-only, for the in-panel shortcut
    editor (``interface/utils/keymap_editor.py``) to resolve each item's
    live, editable counterpart on ``wm.keyconfigs.user``."""
    return list(_keymaps)
