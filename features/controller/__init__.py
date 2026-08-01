"""Controller feature package.

Owns scene-mode switching (Enter/Exit/Toggle Edit Mode, Pose Mode), the
pie-menu shortcut, and the Safe Shrink utility operator. All classes are
registered from the ops_* sub-modules; the UnifiedFeatureExtension hook
lives in controller_feature.py.
"""

from importlib import reload

from . import ops_scene_modes
from . import ops_shortcuts
from . import ops_tools
from . import controller_feature

for mod in (ops_scene_modes, ops_shortcuts, ops_tools, controller_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops_scene_modes.register()
    # ops_tools MUST register before ops_shortcuts -- ops_shortcuts.register()
    # sets keymap item properties (kmi.properties.next) on
    # superskin.scrub_timeline_fast, which ops_tools.py defines. Blender only
    # exposes an operator's custom properties on a keymap item once that
    # operator's class is actually registered; doing this in the other order
    # raised AttributeError: 'OperatorProperties' object has no attribute 'next'.
    ops_tools.register()
    ops_shortcuts.register()
    controller_feature.register()


def unregister():
    controller_feature.unregister()
    ops_shortcuts.unregister()
    ops_tools.unregister()
    ops_scene_modes.unregister()
