"""DeformLayerViewer feature package."""

from importlib import reload

from . import layer_viewer
from . import deform_bone_viewer
from . import deform_layer_viewer_feature

for mod in (layer_viewer, deform_bone_viewer, deform_layer_viewer_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    layer_viewer.register()
    deform_bone_viewer.register()
    deform_layer_viewer_feature.register()


def unregister():
    deform_layer_viewer_feature.unregister()
    deform_bone_viewer.unregister()
    layer_viewer.unregister()
