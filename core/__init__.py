
from importlib import reload

from . import data_models
from . import viewport
from . import shaders
from . import layer_storage
from . import ui_controller
from . import preferences
from . import bone_identity
from . import facade

for mod in (data_models, viewport,
            shaders, layer_storage,
            ui_controller, preferences, bone_identity,
            facade):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    data_models.register()
    shaders.register()
    layer_storage.register()
    ui_controller.register()
    preferences.register()
    bone_identity.register()


def unregister():
    bone_identity.unregister()
    preferences.unregister()
    ui_controller.unregister()
    layer_storage.unregister()
    shaders.unregister()
    data_models.unregister()
