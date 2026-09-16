"""SuperSkinPro UI Controller — private core infrastructure.

Sub-modules only. No public classes remain in this package; UIController
has been fully absorbed into CoreFacade. These sub-modules (layer_crud,
pipeline, operations, undo_manager) accept a CoreFacade-compatible ctrl
parameter and must never be imported from outside core/.
"""

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
