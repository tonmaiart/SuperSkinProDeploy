"""Weight-apply domain package — Add, Scale, Smooth, Sharpen operators +
logic."""

from importlib import reload

from . import ui
from . import logic
from . import draw
from . import ops
from . import weight_apply_feature
from . import keymap

# Deep Matrix Reload Rule: logic-bearing modules before registration
# wrappers.
for mod in (ui, logic, draw, ops, weight_apply_feature, keymap):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    weight_apply_feature.register()
    ops.register()
    keymap.register()


def unregister():
    keymap.unregister()
    ops.unregister()
    weight_apply_feature.unregister()
    draw.cleanup()
