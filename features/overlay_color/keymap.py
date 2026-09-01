"""Keymap registration for overlay_color's Multi Color Preview — Alt+3
binds to toggle_multi_color, a plain press-to-toggle (not a hold gesture)."""

import bpy

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return
    km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
    kmi = km.keymap_items.new(
        "superskin.toggle_multi_color", type='THREE', value='PRESS', alt=True)
    _keymaps.append((km, kmi, "Multi Color Preview"))


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    """Return the ``(km, kmi, label)`` triples registered on the addon
    keyconfig by ``register()`` above, read-only, for the in-panel
    shortcut editor (``interface/utils/keymap_editor.py``) to resolve
    each item's live, editable counterpart on ``wm.keyconfigs.user``."""
    return list(_keymaps)
