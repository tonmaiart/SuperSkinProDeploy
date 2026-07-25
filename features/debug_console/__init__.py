from importlib import reload

from . import debug_console_feature
from . import ops

# debug_console_feature first — ops.py imports get_visible_entries() from it.
for mod in (debug_console_feature, ops):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    debug_console_feature.register()
    ops.register()


def unregister():
    ops.unregister()
    debug_console_feature.unregister()
