
from . import ramps

_GRADIENTS = (("influence", "Bone Weight Gradient"), ("mask", "Mask Weight Gradient"))


def draw_gradients(layout) -> None:
    ramps.sync_from_textures()
    ramps.keep_live_sync()
    from ...interface.template_ui import settings_row
    for key, label in _GRADIENTS:
        value = settings_row(layout, label)
        col = value.column()
        tex = ramps.ramp_texture(key)
        if tex is None:
            ramps.request_textures()
            col.label(text="Preparing the color ramp...")
        else:
            col.template_color_ramp(tex, "color_ramp", expand=True)
        if not ramps.ramp_is_default(key):
            value.operator("superskin.reset_color_ramp", text="", icon='LOOP_BACK').ramp = key
