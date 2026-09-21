"""Bone Picker keymap registration — owned by the bone_picker feature package."""

import bpy

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

    km = kc.keymaps.new(name='Weight Paint', space_type='EMPTY')
    kmi = km.keymap_items.new("object.mw_pick_bone", type='TWO', value='PRESS', alt=True)
    _keymaps.append((km, kmi, "Pick Bone"))


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    """Return the ``(km, kmi, label)`` triples registered on the addon keyconfig by
    ``register()`` above, read-only."""
    return list(_keymaps)
