from importlib import reload

from . import bone_axis
from . import supersample
from . import ops
from . import deform_overlay
from . import keymap
from . import bone_picker_feature
from . import public_api

for mod in (bone_axis, supersample, ops, deform_overlay, keymap, bone_picker_feature, public_api):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    bone_picker_feature.register()
    ops.register()
    keymap.register()
    deform_overlay.register_props()
    deform_overlay.show()


def unregister():
    keymap.unregister()
    deform_overlay.cleanup()
    deform_overlay.unregister_props()
    ops.unregister()
    bone_picker_feature.unregister()
