"""Template UI sub-package — shared UIList mixins, layout helpers, and selection adapters for
feature domains."""

from importlib import reload

from . import base_list
from . import select_ops
from . import layout

from .base_list import SuperSkinListMixin
from .layout import draw_list_with_sidebar, draw_lists_side_by_side
from .select_ops import (
    ListSelectionAdapter,
    register_adapter,
    get_adapter,
    resolve_row_click_selection,
    is_double_click,
    reset_double_click,
    invert_selection,
    set_last_active_domain,
    get_last_active_domain,
)

for mod in (base_list, select_ops, layout):
    try:
        reload(mod)
    except Exception:
        pass
