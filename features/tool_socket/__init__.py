"""Tool Socket feature package — lifecycle and hot-reload bootstrap.

No ops.py/logic.py -- the socket itself has no dispatch actions (actions =
[]) and no persisted settings; it only hosts a dropdown that redraws
whichever plugged-in UnifiedFeatureExtension is currently selected. See
README.md for the plug-in contract.
"""

from importlib import reload
from . import tool_socket_feature

for mod in (tool_socket_feature,):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    tool_socket_feature.register()


def unregister():
    tool_socket_feature.unregister()
