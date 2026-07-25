"""Auto Block Weight — draws its UI section."""


def draw_section(layout):
    # layout.label(text="Auto Assign:")
    col = layout.column(align=True)
    col.scale_y = 1.2
    col.operator(
        "mesh.auto_assign_closest_unlocked_bone",
        text="Auto Assign Weight",
    )
