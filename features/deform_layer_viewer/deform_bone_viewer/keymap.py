"""Keymap registration for DeformBoneViewer — owned by this feature package.

Shortcuts:
  Alt+1 (Mesh mode) -> DISABLED (2026-09-10, per explicit user request).
    Used to trigger superskin.toggle_mask_mode (plain press-to-toggle
    between Edit Bone/Edit Mask), but this keymap registration has been
    removed to reclaim Alt+1 for the (since-removed, per a later explicit
    user request) features/layer_picker domain's hold gesture. Alt+1 was
    unclaimed again for a time once that domain was gone, but has SINCE
    been claimed by `weight_apply`'s Weight Brush/Lasso Select toggle
    (`brush_tool.py::SUPERSKIN_OT_toggle_weight_brush_tool`) -- do not
    re-add a `km.keymap_items.new(...)` block for Alt+1 here, it would
    collide. The `superskin.toggle_mask_mode` operator itself is untouched
    and still fully registered -- reachable from the "Edit Bone"/"Edit
    Mask" buttons in the N-panel (see
    docs/core-interfaces/mode-edit-toggle-widget.md) -- only this shortcut
    binding is gone. If this needs a shortcut again, pick a currently
    unclaimed key, not Alt+1.
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

Alt+1 / Alt+Ctrl+MMB / Alt+Ctrl+RMB above are all currently disabled --
`register()` below has no active `km.keymap_items.new(...)` calls for any
of them, only the commented-out history of what used to be there and why.
Re-enabling any of them means appending its `(km, kmi, label)` tuple to
`_keymaps`, matching every other keymap.py in this codebase's pattern.
"""

import bpy

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

    # Alt+1 -> superskin.toggle_mask_mode: DISABLED. Now claimed by
    # weight_apply's Weight Brush/Lasso Select toggle -- see docstring
    # above. Do not reuse Alt+1 here.

    # Alt+Ctrl+MMB -> object.mw_select_affect_vertices: DISABLED, reclaimed
    # by features/vertex_selector (see docstring above).

    # Alt+Ctrl+RMB -> object.mw_select_affect_boundary: DISABLED, reclaimed
    # by features/weight_apply (see docstring above).


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    """Return the ``(km, kmi, label)`` triples registered on the addon
    keyconfig by ``register()`` above, read-only, for the in-panel
    shortcut editor (``interface/utils/keymap_editor.py``) to resolve
    each item's live, editable counterpart on ``wm.keyconfigs.user``."""
    return list(_keymaps)
