"""PickWalk feature package — lifecycle and hot-reload bootstrap."""

from importlib import reload

from . import logic
from . import pick_walk_feature
from . import ops
from . import keymap

for mod in (logic, pick_walk_feature, ops, keymap):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    pick_walk_feature.register()
    ops.register()
    keymap.register()


def unregister():
    keymap.unregister()
    ops.unregister()
    pick_walk_feature.unregister()
