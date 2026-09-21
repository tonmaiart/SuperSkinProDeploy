"""Weight Brush drawing primitives -- the Projected checkbox and the Hardness slider."""


def draw_projection_button(row, p):
    """Projected checkbox: on selects Screen projection, off selects Surface."""
    row.prop(p, "brush_projected", text="Projected")


def draw_hardness_slider(row, p):
    """Hardness as a plain `[0.0, 1.0]` slider."""
    row.prop(p, "brush_hardness", text="Hardness", slider=True)
