from importlib import reload

from . import engine
from . import ops
from . import addon_updater_feature

for mod in (engine, ops, addon_updater_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    addon_updater_feature.register()


def unregister():
    addon_updater_feature.unregister()
