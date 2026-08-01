from importlib import reload

from . import ops
from . import deform_overlay
from . import keymap
from . import bone_picker_feature

for mod in (ops, deform_overlay, keymap, bone_picker_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    bone_picker_feature.register()
    ops.register()
    keymap.register()
    deform_overlay.show()


def unregister():
    keymap.unregister()
    deform_overlay.cleanup()
    ops.unregister()
    bone_picker_feature.unregister()
