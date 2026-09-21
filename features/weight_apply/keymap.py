"""Keymap registration for the Weight Apply gesture shortcuts — owned by this feature package."""

import bpy

_keymaps = []

_GESTURE_BINDINGS = (
    ("add_scale", 'LEFTMOUSE', "Add / Scale"),
    ("smooth_sharpen", 'RIGHTMOUSE', "Smooth / Sharpen"),
)


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return
    for action, mouse_type, label in _GESTURE_BINDINGS:
        km = kc.keymaps.new(name='Weight Paint', space_type='EMPTY')
        kmi = km.keymap_items.new(
            "superskin.weight_gesture",
            type=mouse_type,
            value='PRESS',
            alt=True,
        )
        kmi.properties.action = action
        _keymaps.append((km, kmi, f"{label} (start normal)"))

        km = kc.keymaps.new(name='Weight Paint', space_type='EMPTY')
        kmi = km.keymap_items.new(
            "superskin.weight_gesture",
            type=mouse_type,
            value='PRESS',
            alt=True,
            ctrl=True,
        )
        kmi.properties.action = action
        _keymaps.append((km, kmi, f"{label} (start fine)"))

    km = kc.keymaps.new(name='Weight Paint', space_type='EMPTY')
    kmi = km.keymap_items.new(
        "superskin.repeat_last_weight_apply",
        type='R',
        value='PRESS',
        shift=True,
    )
    _keymaps.append((km, kmi, "Repeat Last Weight Apply"))


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    """Return the ``(km, kmi, label)`` triples registered on the addon keyconfig by
    ``register()`` above, read-only."""
    return list(_keymaps)
