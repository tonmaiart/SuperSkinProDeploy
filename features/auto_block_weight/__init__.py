"""Auto Block Weight domain package — auto assign closest unlocked bone."""

from importlib import reload

from . import ui
from . import ops
from . import auto_block_feature

for mod in (ui, ops, auto_block_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops.register()
    auto_block_feature.register()


def unregister():
    auto_block_feature.unregister()
    ops.unregister()
