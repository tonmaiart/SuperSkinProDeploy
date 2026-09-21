"""Keymaps for the vertex tool subpackage, all on the Weight Paint keymap."""

import bpy

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

    km = kc.keymaps.new(name='Weight Paint', space_type='EMPTY')

    kmi_add = km.keymap_items.new(
        "view3d.select_circle",
        type='MIDDLEMOUSE', value='PRESS', alt=True, shift=True,
    )
    kmi_add.properties.mode = 'ADD'
    kmi_add.properties.wait_for_input = False
    _keymaps.append((km, kmi_add, "Drag-Select Add (Circle Brush)"))

    kmi_sub = km.keymap_items.new(
        "view3d.select_circle",
        type='MIDDLEMOUSE', value='PRESS', alt=True, ctrl=True,
    )
    kmi_sub.properties.mode = 'SUB'
    kmi_sub.properties.wait_for_input = False
    _keymaps.append((km, kmi_sub, "Drag-Select Remove (Circle Brush)"))

    kmi = km.keymap_items.new(
        "superskin.grow_selection",
        type='WHEELUPMOUSE', value='PRESS', alt=True, ctrl=True,
    )
    _keymaps.append((km, kmi, "Grow Selection"))

    kmi = km.keymap_items.new(
        "superskin.shrink_selection",
        type='WHEELDOWNMOUSE', value='PRESS', alt=True, ctrl=True,
    )
    _keymaps.append((km, kmi, "Shrink Selection"))

    kmi = km.keymap_items.new("superskin.weight_hide_selected", type='H', value='PRESS')
    _keymaps.append((km, kmi, "Hide Selected Vertices"))

    kmi = km.keymap_items.new("superskin.weight_reveal", type='H', value='PRESS', alt=True)
    _keymaps.append((km, kmi, "Reveal Hidden Vertices"))


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    """Return the ``(km, kmi, label)`` triples registered on the addon keyconfig, for the in-
    panel shortcut editor."""
    return list(_keymaps)
