
import bpy

from .circle_select import CIRCLE_SELECT_ENABLED

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

    km = kc.keymaps.new(name='Weight Paint', space_type='EMPTY')

    if CIRCLE_SELECT_ENABLED:
        kmi_sub = km.keymap_items.new(
            "superskin.weight_select_circle",
            type='MIDDLEMOUSE', value='PRESS', alt=True, shift=True,
        )
        kmi_sub.properties.mode = 'SUB'
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

    kmi = km.keymap_items.new("superskin.weight_hide_unselected", type='H', value='PRESS', shift=True)
    _keymaps.append((km, kmi, "Hide Unselected Vertices"))

    kmi = km.keymap_items.new("superskin.weight_reveal", type='H', value='PRESS', alt=True)
    _keymaps.append((km, kmi, "Reveal Hidden Vertices"))


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    return list(_keymaps)
