
from ....interface.utils import session_shortcuts
from .circle_select import CIRCLE_SELECT_ENABLED
from .common import LASSO_TOOL_IDNAME

_keymaps = []


def _bind(idname, label, properties=None, **key):
    bound = session_shortcuts.bind(idname, tool=LASSO_TOOL_IDNAME, properties=properties, **key)
    if bound:
        _keymaps.append((*bound, label))


def register():
    if CIRCLE_SELECT_ENABLED:
        _bind("superskin.weight_select_circle", "Drag-Select Remove (Circle Brush)", {"mode": 'SUB'},
              type='MIDDLEMOUSE', value='PRESS', alt=True, shift=True)
    _bind("superskin.grow_selection", "Grow Selection",
          type='WHEELUPMOUSE', value='PRESS', alt=True, ctrl=True)
    _bind("superskin.shrink_selection", "Shrink Selection",
          type='WHEELDOWNMOUSE', value='PRESS', alt=True, ctrl=True)
    _bind("superskin.weight_hide_selected", "Hide Selected Vertices", type='H', value='PRESS')
    _bind("superskin.weight_hide_unselected", "Hide Unselected Vertices", type='H', value='PRESS', shift=True)
    _bind("superskin.weight_reveal", "Reveal Hidden Vertices", type='H', value='PRESS', alt=True)


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    return list(_keymaps)
