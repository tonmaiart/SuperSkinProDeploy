"""LayerViewer subpackage."""

from importlib import reload

from . import object_selector
from . import ops
from . import ui
from . import public_api

for mod in (object_selector, ops, ui, public_api):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    object_selector.register()
    ops.register()
    ui.register()


def unregister():
    ui.unregister()
    ops.unregister()
    object_selector.unregister()
