"""DeformLayerViewer feature package.

Merges the former standalone `layer_viewer` (LAYER tab) and
`deform_bone_viewer` (SKINNING tab) domains into one UnifiedFeatureExtension,
`DeformLayerViewerFeature` (`deform_layer_viewer_feature.py`), registered
under both tabs -- both lists are genuinely used together across Object and
Edit Mode. The two subfolders keep their original names and contents; only
this package's own registration wiring is new. See
docs/domains/deform_layer_viewer.md for the full rationale.
"""

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
