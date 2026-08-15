"""Keymap registration for DeformBoneViewer — owned by this feature package.

Shortcuts:
  Alt+1 (Mesh mode) -> superskin.toggle_mask_mode. Plain press-to-toggle
    (not a hold gesture), mirrors overlay_color's Alt+3 pattern. Frees up
    Alt+1 from the shortcut-overlay HUD toggle, which moved to Alt+4 (see
    interface/utils/shortcut_overlay.py).
  Alt+Ctrl+MMB (Mesh mode) -> DISABLED. Used to trigger
    `object.mw_select_affect_vertices` ("Select Affect Vertices" -- every
    vertex with weight/mask > 0; see `ops.py` for the operator itself), but
    this keymap registration has been removed to reclaim Alt+Ctrl+MMB for
    `features/vertex_selector`'s hold+drag pick-walk gesture (see that
    domain's README). The operator itself is untouched and still fully
    registered -- only this shortcut binding is gone. Easily reversible:
    re-add the `km.keymap_items.new(...)` block below if this needs to
    come back.
  Alt+Ctrl+RMB (Mesh mode) -> DISABLED. Used to trigger
    `object.mw_select_affect_boundary` (selects vertices sitting at the
    boundary/junction between the unweighted (0) and weighted region --
    any vertex whose own weight-state differs from at least one mesh
    neighbor's; a separate operator from `mw_select_affect_vertices` above,
    deliberately, so neither shortcut shows a redo/options popup at the
    bottom-left), but this keymap registration has been removed to reclaim
    Alt+Ctrl+RMB for `features/weight_apply`'s "start Smooth/Sharpen gesture
    already in slow/fine-precision mode" shortcut (see that domain's
    README/keymap.py). The operator itself is untouched and still fully
    registered -- only this shortcut binding is gone. Easily reversible:
    re-add the `km.keymap_items.new(...)` block below if this needs to
    come back.

The Alt+Ctrl+MMB / Alt+Ctrl+RMB shortcuts above are currently disabled --
`register()` below has no active `km.keymap_items.new(...)` calls for
either, only the commented-out history of what used to be there and why.
Re-enabling either shortcut means appending its `(km, kmi)` tuple to
`_keymaps`, matching the Alt+1 registration above and every other
keymap.py in this codebase's pattern.
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
        "superskin.toggle_mask_mode", type='ONE', value='PRESS', alt=True)
    _keymaps.append((km, kmi))

    # Alt+Ctrl+MMB -> object.mw_select_affect_vertices: DISABLED, reclaimed
    # by features/vertex_selector (see docstring above).

    # Alt+Ctrl+RMB -> object.mw_select_affect_boundary: DISABLED, reclaimed
    # by features/weight_apply (see docstring above).


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
