"""Keymap registration for Pick Walk — owned by this feature package.

Shortcut:
  Alt+Ctrl+MMB hold+drag (Mesh mode) -> `superskin.pick_walk` -- Maya-style
  pick walk: shifts the whole selection to the neighboring loop in the drag
  direction. See ops.py for the modal gesture itself.

This reclaims Alt+Ctrl+MMB from `object.mw_select_affect_vertices`
("Select Affect Vertices"), which `features/deform_bone_viewer/keymap.py`
used to bind it to. That binding is now disabled there -- the operator
itself is untouched and still fully registered, just unreachable via this
shortcut -- see that domain's README for the "Temporarily hidden" precedent
this follows.
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
        "superskin.pick_walk",
        type='MIDDLEMOUSE',
        value='PRESS',
        alt=True,
        ctrl=True,
    )
    _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
