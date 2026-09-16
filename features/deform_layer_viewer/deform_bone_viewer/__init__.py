"""DeformBoneViewer subpackage -- the SKINNING-tab half of the merged
`deform_layer_viewer` domain (see docs/domains/deform_layer_viewer.md).

Anchors the Deform Bone List at the top of the SKINNING tab as a
non-collapsible viewer, drawn by DeformBoneViewerFeature (still a
UnifiedFeatureExtension subclass, but no longer registered with
UnifiedRegistry directly -- the package parent,
`deform_layer_viewer_feature.py`, instantiates it and delegates to its
execute()/draw_section()). All UIList, adapter, and operator classes are
registered from ui.py.
"""

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
