"""LayerViewer feature package.

Anchors the Layer List at the top of the LAYER tab as a non-collapsible
viewer via LayerViewerFeature (UnifiedFeatureExtension). All UIList, adapter,
and operator classes are registered from ui.py.
"""

from importlib import reload

from . import object_selector
from . import ops
from . import ui
from . import layer_viewer_feature

for mod in (object_selector, ops, ui, layer_viewer_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    object_selector.register()
    ops.register()
    ui.register()
    layer_viewer_feature.register()


def unregister():
    layer_viewer_feature.unregister()
    ui.unregister()
    ops.unregister()
    object_selector.unregister()
