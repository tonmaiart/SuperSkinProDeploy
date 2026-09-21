"""Activate feature package — lifecycle and hot-reload bootstrap."""

from importlib import reload

from . import activate_feature

for mod in (activate_feature,):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    activate_feature.register()


def unregister():
    activate_feature.unregister()
