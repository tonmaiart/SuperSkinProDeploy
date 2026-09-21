"""Weight Brush -- hover-preview keymap."""

import bpy

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc is None:
        return
    km = kc.keymaps.new(name='Weight Paint', space_type='EMPTY')
    kmi = km.keymap_items.new("superskin.weight_brush_hover", type='MOUSEMOVE', value='ANY', any=True)
    _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
