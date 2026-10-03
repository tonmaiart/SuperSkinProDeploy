
from importlib import reload

from . import ops
from . import clipboard_logic
from . import clipboard_ops
from . import move_weight_logic
from . import move_weight_ops
from . import ui
from . import keymap
from . import deform_bone_viewer_feature

for mod in (ops, clipboard_logic, clipboard_ops, move_weight_logic, move_weight_ops, ui, keymap, deform_bone_viewer_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops.register()
    clipboard_ops.register()
    move_weight_ops.register()
    ui.register()
    keymap.register()


def unregister():
    keymap.unregister()
    ui.unregister()
    move_weight_ops.unregister()
    clipboard_ops.unregister()
    ops.unregister()
