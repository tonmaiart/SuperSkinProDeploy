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
    _keymaps.append((km, kmi, "Grow Selection"))

    km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
    kmi = km.keymap_items.new(
        "superskin.grow_shrink_selection",
        type='WHEELDOWNMOUSE',
        value='PRESS',
        alt=True,
        ctrl=True,
    )
    kmi.properties.direction = -1
    _keymaps.append((km, kmi, "Shrink Selection"))


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    """Return the ``(km, kmi, label)`` triples registered on the addon
    keyconfig by ``register()`` above, read-only, for the in-panel
    shortcut editor (``interface/utils/keymap_editor.py``) to resolve
    each item's live, editable counterpart on ``wm.keyconfigs.user`` --
    by ``idname`` plus creation-rank, since both items here share the
    same idname (``superskin.grow_shrink_selection``); see that module's
    docstring for why rank, not ``KeyMapItem.id``, disambiguates them."""
    return list(_keymaps)
