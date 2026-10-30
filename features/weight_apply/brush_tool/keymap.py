
from ....interface.utils import session_shortcuts

_keymaps = []


def register():
    bound = session_shortcuts.observe("superskin.weight_brush_hover", type='MOUSEMOVE', value='ANY', any=True)
    if bound:
        _keymaps.append(bound)


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
