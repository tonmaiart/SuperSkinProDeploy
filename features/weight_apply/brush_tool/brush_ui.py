

def draw_projection_button(row, p):
    row.prop(p, "brush_projection", expand=True)


def draw_hardness_slider(row, p):
    row.prop(p, "brush_hardness", text="Hardness", slider=True)


_PRESSURE_TOGGLES = (
    ("use_pressure_size", "Size"),
    ("use_pressure_hardness", "Hardness"),
    ("use_pressure_strength", "Strength"),
)
_PRESSURE_TOGGLE_UNITS_X = 4.0


def draw_pressure_toggles(layout, p):
    row = layout.row(align=True)
    for prop, label in _PRESSURE_TOGGLES:
        sub = row.row(align=True)
        sub.ui_units_x = _PRESSURE_TOGGLE_UNITS_X
        sub.prop(p, prop, text=label, icon='STYLUS_PRESSURE', toggle=True)
