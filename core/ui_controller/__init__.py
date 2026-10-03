
from importlib import reload

from . import undo_manager
from . import layer_crud
from . import operations
from . import pipeline


for mod in (undo_manager, layer_crud, operations, pipeline):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    undo_manager.register()


def unregister():
    undo_manager.unregister()
