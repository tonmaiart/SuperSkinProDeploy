"""Skin Tools UI feature package."""

from importlib import reload

from . import (
    ui_weight_apply, ui_mirror, ui_action_grid,
)
from . import skin_tools_ui_feature

# Bottom-up reload -- widget modules before the orchestrating feature class.
_widget_modules = (
    ui_weight_apply, ui_mirror, ui_action_grid,
)
for mod in _widget_modules + (skin_tools_ui_feature,):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ui_mirror.register()
    ui_weight_apply.register()
    skin_tools_ui_feature.register()


def unregister():
    skin_tools_ui_feature.unregister()
    ui_weight_apply.unregister()
    ui_mirror.unregister()
