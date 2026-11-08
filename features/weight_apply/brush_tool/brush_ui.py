

def draw_projection_button(row, p):
    draw_brush_mode(row, p)
    row.separator()
    row.prop(p, "connected_only")


def draw_brush_mode(row, p):
    row.prop(p, "brush_mode", expand=True)


def draw_connected_only_icon(layout, p, icon_scale_x=1.0):
    from ....interface.utils.icons import get_connected_only_icon_id
    icon = layout.row(align=True)
    icon.scale_x = icon_scale_x
    _icon_toggle(icon, p, "connected_only", get_connected_only_icon_id(), 'LINKED')


def _icon_toggle(row, p, prop, icon_id, fallback):
    if icon_id:
        row.prop(p, prop, text="", icon_value=icon_id, toggle=True)
    else:
        row.prop(p, prop, text="", icon=fallback, toggle=True)


def draw_size_row(layout, p, icon_scale_x=1.0):
    row = layout.row(align=True)
    row.prop(p, "brush_radius", text="Size")
    pressure = row.row(align=True)
    pressure.scale_x = icon_scale_x
    pressure.prop(p, "use_pressure_strength", text="", icon='STYLUS_PRESSURE', toggle=True)


def draw_falloff_buttons(row, p):
    row.prop(p, "falloff", expand=True, icon_only=True)
