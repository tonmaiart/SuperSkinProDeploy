"""DeformBoneViewer feature package.

Anchors the Deform Bone List at the top of the SKINNING tab as a
non-collapsible viewer via DeformBoneViewerFeature (UnifiedFeatureExtension).
All UIList, adapter, and operator classes are registered from ui.py.
"""

from importlib import reload

from . import ops
from . import ui
from . import keymap
from . import deform_bone_viewer_feature

for mod in (ops, ui, keymap, deform_bone_viewer_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops.register()
    ui.register()
    keymap.register()
    deform_bone_viewer_feature.register()


def unregister():
    deform_bone_viewer_feature.unregister()
    keymap.unregister()
    ui.unregister()
    ops.unregister()
