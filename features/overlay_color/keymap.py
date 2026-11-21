
from ...interface.utils import session_shortcuts

_keymaps = []


def register():
    bound = session_shortcuts.bind("superskin.toggle_multi_color", type='THREE', value='PRESS', alt=True)
    if bound:
        bound[1].repeat = False
        _keymaps.append((*bound, "Toggle Multi Color Display"))


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    return list(_keymaps)
