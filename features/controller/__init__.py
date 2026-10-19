
from importlib import reload

from . import native_weight_guard
from . import ops_scene_modes
from . import controller_feature
from . import public_api

for mod in (native_weight_guard, ops_scene_modes, controller_feature, public_api):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops_scene_modes.register()
    controller_feature.register()


def unregister():
    controller_feature.unregister()
    ops_scene_modes.unregister()
