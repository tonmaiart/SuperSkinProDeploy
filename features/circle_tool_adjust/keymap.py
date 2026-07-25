"""Keymap registration for CircleToolAdjust — owned by this feature package.

Shortcuts:
  Alt+Shift+RMB (Mesh mode) -> `superskin.circle_tool_adjust_radius`. A
    plain click toggles between Select Circle and Select Box; hold+drag
    adjusts the circle-select brush radius (Select Circle tool only). See
    ops.py for the full contract.
  Alt+Ctrl+Scroll Up/Down (Mesh mode) -> `superskin.grow_shrink_selection`
    -- one Grow/Shrink step per wheel notch. Merged in from the former
    `auto_grow` domain, which used to bind this to Alt+Shift+MMB
    click/hold-drag instead; see the domain's README for why scroll
    replaced that gesture entirely (no more left/right split-by-click-
    position, no more hold-drag slider). Moved again from Alt+Shift+Scroll
    to Alt+Ctrl+Scroll, freeing plain Shift as a modifier for other
    gestures.

The radius/tool-toggle binding moved several times: originally plain
Alt+LMB, then Alt+Ctrl+LMB, then plain Alt+RMB, now Alt+Shift+RMB -- each
move made room for (or reclaimed room from) a Weight Apply gesture
shortcut (`features/weight_apply/keymap.py`).
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
        "superskin.circle_tool_adjust_radius",
        type='RIGHTMOUSE',
        value='PRESS',
        alt=True,
        shift=True,
    )
    _keymaps.append((km, kmi))

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
