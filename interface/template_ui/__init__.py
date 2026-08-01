"""Template UI sub-package — shared UIList mixins, layout helpers, and
selection adapters for feature domains.

Exports (public):
    SuperSkinListMixin          - mixin for concrete bpy.types.UIList subclasses
    draw_list_with_sidebar      - layout helper (template_list + search + side buttons)
    register_adapter            - register a ListSelectionAdapter for a domain
    resolve_row_click_selection - pure function: modifier-key range-select / toggle logic
    ListSelectionAdapter        - ABC for domain-specific multi-select state
    get_adapter                 - retrieve a registered adapter by domain key
"""

from importlib import reload

from . import base_list
from . import select_ops
from . import layout

from .base_list import SuperSkinListMixin
from .layout import draw_list_with_sidebar
from .select_ops import (
    ListSelectionAdapter,
    register_adapter,
    get_adapter,
    resolve_row_click_selection,
)

for mod in (base_list, select_ops, layout):
    try:
        reload(mod)
    except Exception:
        pass
