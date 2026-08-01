"""Keymap registration for CircleToolAdjust — owned by this feature package.

Shortcut:
  Alt+Shift+RMB (Mesh mode) -> `superskin.circle_tool_adjust_radius`. A
    plain click toggles between Select Circle and Select Box; hold+drag
    adjusts the circle-select brush radius (Select Circle tool only). See
    ops.py for the full contract.

Grow/Shrink Selection's Alt+Ctrl+Scroll binding (formerly registered here,
merged in from the former `auto_grow` domain) has moved to
`features/vertex_selector/keymap.py` along with the operator itself.

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


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
