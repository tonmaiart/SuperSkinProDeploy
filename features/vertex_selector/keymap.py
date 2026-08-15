"""Keymap registration for VertexSelector — owned by this feature package.

Shortcuts:
  Alt+Ctrl+Scroll Up/Down (Mesh mode) -> `superskin.grow_shrink_selection`
    -- hop-count Grow/Shrink, one step per wheel notch. Moved here from
    `features/circle_tool_adjust/keymap.py` unchanged.

Pick Walk (`superskin.pick_walk`, Alt+Ctrl+MMB) and the geodesic-distance
Grow/Shrink mode (Ctrl+Alt+Shift+Scroll) have been removed entirely --
their keymap bindings are gone along with the operators/logic backing them.
"""

import bpy

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

    km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
    kmi = km.keymap_items.new(
        "superskin.grow_shrink_selection",
        type='WHEELUPMOUSE',
        value='PRESS',
        alt=True,
        ctrl=True,
    )
    kmi.properties.direction = 1
    _keymaps.append((km, kmi))

    km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
    kmi = km.keymap_items.new(
        "superskin.grow_shrink_selection",
        type='WHEELDOWNMOUSE',
        value='PRESS',
        alt=True,
        ctrl=True,
    )
    kmi.properties.direction = -1
    _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
