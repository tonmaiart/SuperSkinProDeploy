"""LayerViewer subpackage -- the LAYER-tab half of the merged
`deform_layer_viewer` domain (see docs/domains/deform_layer_viewer.md).

Anchors the Layer List at the top of the LAYER tab as a non-collapsible
viewer, drawn by LayerViewerFeature (still a UnifiedFeatureExtension
subclass, but no longer registered with UnifiedRegistry directly -- the
package parent, `deform_layer_viewer_feature.py`, instantiates it and
delegates to its draw_section()). All UIList, adapter, and operator classes
are registered from ui.py.
"""

from importlib import reload

from . import object_selector
from . import ops
from . import ui
from . import public_api
from . import layer_viewer_feature

for mod in (object_selector, ops, ui, public_api, layer_viewer_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    object_selector.register()
    ops.register()
    ui.register()


def unregister():
    ui.unregister()
    ops.unregister()
    object_selector.unregister()
