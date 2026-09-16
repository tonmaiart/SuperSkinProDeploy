"""LassoToolAdjust feature package -- lifecycle and hot-reload bootstrap."""

from importlib import reload

from . import lasso_tool_adjust_feature
from . import ops
from . import keymap
from . import public_api

# `public_api.py` re-exports `LASSO_TOOL_IDNAME` from
# `lasso_tool_adjust_feature` -- reloaded AFTER it, same Deep Matrix Reload
# Rule ordering `circle_tool_adjust/__init__.py` used to follow (foundations
# before wrappers), even though a plain string constant has no stale-
# reference risk the way a stale function reference would.
for mod in (lasso_tool_adjust_feature, ops, keymap, public_api):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    lasso_tool_adjust_feature.register()
    ops.register()
    keymap.register()


def unregister():
    keymap.unregister()
    ops.unregister()
    lasso_tool_adjust_feature.unregister()
