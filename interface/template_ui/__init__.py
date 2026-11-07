
from importlib import reload

from . import base_list
from . import select_ops
from . import layout
from . import select_all_op

from .base_list import SuperSkinListMixin
from .layout import draw_list_with_sidebar, draw_lists_side_by_side
from .select_ops import (
    ListSelectionAdapter,
    register_adapter,
    get_adapter,
    resolve_row_click_selection,
    is_double_click,
    reset_double_click,
)

for mod in (base_list, select_ops, layout, select_all_op):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    select_all_op.register()


def unregister():
    select_all_op.unregister()
