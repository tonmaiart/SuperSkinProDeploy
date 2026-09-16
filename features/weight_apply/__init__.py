"""Weight-apply domain package — Add, Scale, Smooth, Sharpen operators +
logic."""

from importlib import reload

from . import logic
from . import draw
from . import ops
from . import weight_apply_feature
from . import keymap
from . import brush
from .brush import (
    brush_logic, brush_draw, brush_ops, brush_hover, brush_radius_adjust, brush_tool, brush_ui,
)
from .brush import keymap as brush_keymap

# Deep Matrix Reload Rule: logic-bearing modules before registration
# wrappers.
for mod in (logic, draw, ops, weight_apply_feature, keymap,
            brush, brush_logic, brush_draw, brush_ui, brush_ops, brush_hover,
            brush_radius_adjust, brush_tool, brush_keymap):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    weight_apply_feature.register()
    ops.register()
    keymap.register()
    draw.register()
    if brush.BRUSH_ENABLED:
        brush_ops.register()
        brush_hover.register()
        brush_keymap.register()
        brush_radius_adjust.register()
        brush_tool.register()


def unregister():
    if brush.BRUSH_ENABLED:
        brush_tool.unregister()
        brush_radius_adjust.unregister()
        brush_keymap.unregister()
        brush_hover.unregister()
        brush_ops.unregister()
        brush_draw.cleanup()
    keymap.unregister()
    ops.unregister()
    weight_apply_feature.unregister()
    draw.cleanup()
