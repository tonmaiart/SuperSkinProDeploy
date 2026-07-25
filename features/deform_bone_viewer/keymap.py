"""Keymap registration for DeformBoneViewer — owned by this feature package.

Shortcuts:
  Alt+Ctrl+MMB (Mesh mode) -> DISABLED. Used to trigger
    `object.mw_select_affect_vertices` ("Select Affect Vertices" -- every
    vertex with weight/mask > 0; see `ops.py` for the operator itself), but
    this keymap registration has been removed to reclaim Alt+Ctrl+MMB for
    `features/pick_walk`'s hold+drag pick-walk gesture (see that domain's
    README). The operator itself is untouched and still fully registered --
    only this shortcut binding is gone. Easily reversible: re-add the
    `km.keymap_items.new(...)` block below if this needs to come back.
  Alt+Ctrl+RMB (Mesh mode) -> `object.mw_select_affect_boundary` -- a
    separate operator that selects vertices sitting at the boundary/
    junction between the unweighted (0) and weighted region instead (any
    vertex whose own weight-state differs from at least one mesh
    neighbor's). Deliberately a distinct operator with no adjustable
    properties, rather than a property flag on the operator above, so
    neither shortcut shows a redo/options popup at the bottom-left.

Both entries reference their operator by its `bl_idname` string like every
other keymap.py in this codebase (not a Python import), so this doesn't
violate Zero Cross-Imports even for callers outside this package.
"""

import bpy

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

    # Alt+Ctrl+MMB -> object.mw_select_affect_vertices: DISABLED, reclaimed
    # by features/pick_walk (see docstring above).

    km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
    kmi = km.keymap_items.new(
        "object.mw_select_affect_boundary",
        type='RIGHTMOUSE',
        value='PRESS',
        alt=True,
        ctrl=True,
    )
    _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
