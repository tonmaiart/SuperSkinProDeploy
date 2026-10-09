from importlib import reload

from . import shader_utils
from . import shader_manager

for mod in (shader_utils, shader_manager):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    shader_utils.register()
    shader_manager.register()


def unregister():
    shader_manager.unregister()
    shader_utils.unregister()
