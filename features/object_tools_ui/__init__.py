"""Object Tools UI feature package."""

from importlib import reload

from . import ui_weight_transfer
from . import object_tools_ui_feature

# Bottom-up reload -- widget modules before the orchestrating feature class.
for mod in (ui_weight_transfer, object_tools_ui_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ui_weight_transfer.register()
    object_tools_ui_feature.register()


def unregister():
    object_tools_ui_feature.unregister()
    ui_weight_transfer.unregister()
