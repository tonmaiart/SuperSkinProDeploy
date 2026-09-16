"""Skin Tools UI feature package.

Owns the SKINNING-tab ("Edit weight mode UI") body -- the primary
section-drawing loop plus the actual widget code for every migrated
SKINNING-tab domain (see ``docs/domains/skin_tools_ui.md``):
``ui_weight_apply.py``, ``ui_mirror.py`` (registers
``SUPERSKIN_UL_mirror_sr``/``SUPERSKIN_PT_mirror_options`` -- its own
``draw_section()`` is gone, folded into the grid below),
``ui_weight_info.py`` (also registers its PropertyGroups/UIList),
``ui_sketch_weight.py``, and ``ui_action_grid.py`` (the combined 2-column
grid for Auto Block Weight / Mirror Weight / Copy / Paste / Mark Source /
Transfer -- formerly ``ui_auto_block.py``, ``ui_clipboard.py`` and
``ui_in_mesh_transfer.py``, now deleted).
"""

from importlib import reload

from . import (
    ui_weight_apply, ui_mirror, ui_weight_info, ui_sketch_weight,
    ui_action_grid,
)
from . import skin_tools_ui_feature

# Bottom-up reload -- widget modules before the orchestrating feature class.
_widget_modules = (
    ui_weight_apply, ui_mirror, ui_weight_info, ui_sketch_weight,
    ui_action_grid,
)
for mod in _widget_modules + (skin_tools_ui_feature,):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ui_mirror.register()
    ui_weight_info.register()
    skin_tools_ui_feature.register()


def unregister():
    skin_tools_ui_feature.unregister()
    ui_weight_info.unregister()
    ui_mirror.unregister()
