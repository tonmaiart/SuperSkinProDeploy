
import blf
import bpy


def fit_width_units(context, names, chrome_units, min_units, max_units):
    if not names:
        return min_units
    prefs = context.preferences
    scale = prefs.system.ui_scale
    blf.size(0, prefs.ui_styles[0].widget.points * scale)
    text_px = max(blf.dimensions(0, name)[0] for name in names)
    units = text_px / (20.0 * scale) + chrome_units
    return int(min(max(units + 0.5, min_units), max_units))


def open_popover_at_width(panel_cls, units):
    if panel_cls.bl_ui_units_x != units:
        bpy.utils.unregister_class(panel_cls)
        panel_cls.bl_ui_units_x = units
        bpy.utils.register_class(panel_cls)
    bpy.ops.wm.call_panel('INVOKE_DEFAULT', name=panel_cls.bl_idname, keep_open=True)
