"""Weight Brush panel row -- Radius/Falloff/Strength controls.

Called from `../ui.py`'s `draw_section()`, appended below the existing
Add/Scale/Smooth/Sharpen rows so the whole domain still reads as one
section in the panel, even though the brush's own code lives in this
subfolder.

Same `SSPrefWeightBrush` prefs are also exposed in the 3D-viewport header
while the Weight Brush WorkSpaceTool is active (`brush_tool.py`'s
`draw_settings()`) -- both read/write the same PropertyGroup, so editing
one updates the other. No Mode field here -- which action a dab performs
(Add/Smooth/Scale/Sharpen) is read live from the held modifier key
(`brush_ops.py::_resolve_mode()`), not a stored setting.
"""


def draw_brush_row(layout):
    from .brush_ops import get_brush_prefs
    p = get_brush_prefs()

    layout.separator(factor=1.0)
    layout.label(text="Brush (select the tool in the Toolbar)")
    col = layout.column(align=True)
    col.label(text="Add / Shift: Smooth / Ctrl: Scale / Alt: Sharpen")
    col.prop(p, "brush_projection", text="")
    col.prop(p, "brush_radius", slider=True)
    col.prop(p, "brush_falloff", slider=True)
    col.prop(p, "brush_strength", slider=True)
    col.label(text="F / Shift+F / Ctrl+F: adjust interactively")
