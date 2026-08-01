"""CircleToolAdjust feature package — lifecycle and hot-reload bootstrap."""

from importlib import reload

from . import circle_tool_adjust_feature
from . import ops
from . import keymap

for mod in (circle_tool_adjust_feature, ops, keymap):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    circle_tool_adjust_feature.register()
    ops.register()
    keymap.register()


def unregister():
    keymap.unregister()
    ops.unregister()
    circle_tool_adjust_feature.unregister()
