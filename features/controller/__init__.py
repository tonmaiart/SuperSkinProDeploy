
from importlib import reload

import bpy

from . import native_weight_guard
from . import frame_bone
from . import ops_scene_modes
from . import controller_feature
from . import public_api

for mod in (native_weight_guard, frame_bone, ops_scene_modes, controller_feature, public_api):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    for cls in frame_bone.classes:
        bpy.utils.register_class(cls)
    bpy.app.handlers.depsgraph_update_post.append(frame_bone.watch_active_bone)
    ops_scene_modes.register()
    controller_feature.register()


def unregister():
    controller_feature.unregister()
    ops_scene_modes.unregister()
    try:
        bpy.app.handlers.depsgraph_update_post.remove(frame_bone.watch_active_bone)
    except ValueError:
        pass
    for cls in reversed(frame_bone.classes):
        bpy.utils.unregister_class(cls)
