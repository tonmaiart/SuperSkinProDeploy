"""Controller feature package."""

from importlib import reload

from . import native_weight_guard
from . import ops_scene_modes
from . import ops_shortcuts
from . import ops_tools
from . import controller_feature

for mod in (native_weight_guard, ops_scene_modes, ops_shortcuts, ops_tools, controller_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops_scene_modes.register()
    ops_tools.register()
    ops_shortcuts.register()
    controller_feature.register()


def unregister():
    controller_feature.unregister()
    ops_shortcuts.unregister()
    ops_tools.unregister()
    ops_scene_modes.unregister()
