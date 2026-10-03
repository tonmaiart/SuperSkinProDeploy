

def draw_projection_button(row, p):
    row.prop(p, "brush_projection", expand=True)


def draw_hardness_slider(row, p):
    row.prop(p, "brush_hardness", text="Hardness", slider=True)
