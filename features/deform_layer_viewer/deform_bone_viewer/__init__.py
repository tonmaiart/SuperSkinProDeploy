"""DeformBoneViewer subpackage."""

from importlib import reload

from . import ops
from . import clipboard_logic
from . import clipboard_ops
from . import ui
from . import draw
from . import keymap
from . import deform_bone_viewer_feature

for mod in (ops, clipboard_logic, clipboard_ops, ui, draw, keymap, deform_bone_viewer_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops.register()
    clipboard_ops.register()
    ui.register()
    draw.register()
    keymap.register()


def unregister():
    keymap.unregister()
    draw.unregister()
    ui.unregister()
    clipboard_ops.unregister()
    ops.unregister()
