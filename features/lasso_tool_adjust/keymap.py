"""Keymap registration for lasso_tool_adjust -- also owns two drag-select
gestures built directly on Blender's native `view3d.select_circle`
operator (add-to-selection and remove-from-selection), per explicit user
request, alongside this domain's own Lasso Select tool integration
(`lasso_tool_adjust_feature.py`).

Shortcuts (Mesh Edit Mode), both hold+drag:
  Alt+Shift+MMB -> view3d.select_circle, mode='ADD'    (paint-select ADDS
                   every vertex the circle brush passes over while held)
  Alt+Ctrl+MMB  -> view3d.select_circle, mode='SUB'    (paint-select
                   REMOVES every vertex the circle brush passes over)

**Why Alt+Ctrl+MMB, not Ctrl+Shift+MMB:** Ctrl+Shift+MMB was tried first
(per the original request, mirroring Add's Alt+Shift+MMB by swapping
Alt for Ctrl) but never fired -- Blender's own "3D View" keymap already
binds Ctrl+Shift+MMB to `view3d.dolly` (native Dolly Zoom navigation),
and that space-level binding wins over this addon's 'Mesh'-keymap entry
for the exact same combo. In fact EVERY plain/Shift/Ctrl/Ctrl+Shift
MMB combo is already claimed by native viewport navigation (MMB=Rotate,
Shift+MMB=Pan, Ctrl+MMB=Zoom, Ctrl+Shift+MMB=Dolly) -- only combos that
also hold Alt are free, which is exactly why Add's own Alt+Shift+MMB
works. Alt+Ctrl+MMB mirrors Add the other way (swap Shift for Ctrl,
keep Alt) and doesn't collide with anything.

Both bind straight to the native operator -- no custom Python operator
wrapper needed, mirroring how Blender's own default keymap binds keys
directly to `view3d.select_box`/`select_circle`/`select_lasso` without an
intermediate operator class of its own.

`wait_for_input=False` makes the paint-select begin immediately from the
invoking MMB press (Blender's own gesture system then finishes the drag on
release of that SAME button) instead of arming and waiting for a SECOND
click, which is what a keyboard-only trigger (no mouse button already
down) would need instead.

`mode` is set explicitly on each keymap item rather than left for
Blender's own live-Ctrl-toggle heuristic (box/circle/lasso select flip
ADD<->SUB while Ctrl is held, when the caller did not already pin `mode`)
to decide -- otherwise the Ctrl held as PART OF the remove gesture's own
trigger combo could be misread as a live "invert back to ADD" signal for
the whole drag. This is the same fixed-value-per-binding convention
`weight_apply/keymap.py`'s Alt+LMB/Alt+RMB gestures already use (a fixed
`action` StringProperty per binding, no mid-gesture mode toggle), for the
same reason.

Alt+Shift+MMB was freed by `weight_apply`'s own Weight Brush/Lasso Select
toggle (`SUPERSKIN_OT_toggle_weight_brush_tool`) moving from Alt+Shift+MMB
to Alt+1 -- see `docs/domains/weight_apply.md`. Alt+Ctrl+MMB was
previously unclaimed by anything in this addon -- the last thing to hold
it was `vertex_selector`'s Pick Walk gesture, which has since been removed
entirely (see `docs/domains/vertex_selector.md`).
"""

import bpy

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

    km = kc.keymaps.new(name='Mesh', space_type='EMPTY')

    kmi_add = km.keymap_items.new(
        "view3d.select_circle",
        type='MIDDLEMOUSE', value='PRESS', alt=True, shift=True,
    )
    kmi_add.properties.mode = 'ADD'
    kmi_add.properties.wait_for_input = False
    _keymaps.append((km, kmi_add, "Drag-Select Add (Circle Brush)"))

    kmi_sub = km.keymap_items.new(
        "view3d.select_circle",
        type='MIDDLEMOUSE', value='PRESS', alt=True, ctrl=True,
    )
    kmi_sub.properties.mode = 'SUB'
    kmi_sub.properties.wait_for_input = False
    _keymaps.append((km, kmi_sub, "Drag-Select Remove (Circle Brush)"))


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
