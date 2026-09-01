"""SuperSkinPro core module — orchestration layer bridging Blender and core_subsystems.

No bpy.ops usage; only bpy data-block access (bpy.types, bpy.data, bpy.context).
Reload order: core_subsystems (in top-level __init__.py) must be reloaded before
this module so shaders' direct imports from core_subsystems rebind correctly.
"""

from importlib import reload

from . import prop_callbacks
from . import data_models
from . import shaders
from . import layer_storage
from . import ui_controller
from . import preferences
from . import bone_identity
from . import facade

for mod in (prop_callbacks, data_models,
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
