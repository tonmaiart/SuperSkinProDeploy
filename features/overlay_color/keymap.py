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
    _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
