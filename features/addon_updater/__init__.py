from importlib import reload

from . import engine
from . import ops
from . import addon_updater_feature

# Deep Matrix Reload Rule: bottom-up order -- engine (vendored logic, no bpy
# registration) reloads before ops (operators reference engine.Updater at
# class-definition time), which reloads before addon_updater_feature (the
# UnifiedFeatureExtension wrapper that imports from both).
for mod in (engine, ops, addon_updater_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    addon_updater_feature.register()


def unregister():
    addon_updater_feature.unregister()
