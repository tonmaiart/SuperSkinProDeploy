"""Object Tools UI feature package.

Owns the LAYER-tab ("Object mode UI") body -- the primary section-drawing
loop plus the actual widget code for every migrated LAYER-tab domain (see
``docs/domains/object_tools_ui.md``): ``ui_weight_transfer.py`` (the
weight_transfer/weight_export/weight_import trio, which also registers its
own ``SUPERSKIN_UL_wt_entries`` UIList).
"""

from importlib import reload

from . import ui_weight_transfer
from . import object_tools_ui_feature

# Bottom-up reload -- widget modules before the orchestrating feature class.
for mod in (ui_weight_transfer, object_tools_ui_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ui_weight_transfer.register()
    object_tools_ui_feature.register()


def unregister():
    object_tools_ui_feature.unregister()
    ui_weight_transfer.unregister()
