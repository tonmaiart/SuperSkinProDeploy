"""Keymap registration for overlay_color's Multi Color Preview (the
per-bone preview, `multi_color_draw.py`) -- Alt+3, 'Mesh' keymap category
only, so it only ever fires in Edit Mesh mode.

Multi Color Mask Preview (`multi_color_mask_draw.py`) used to share this
same Alt+3 key as its own manual toggle on the 'Object Mode'/'Pose'
categories. That binding was removed per explicit user request: the mask
preview auto-activated instead, driven by the (since removed, per a later
explicit user request) `features/layer_picker` domain's Alt+1 modal (or
`bone_picker`'s Alt+2-while-masking redirect into it, also removed) via
`superskin.start_multi_color_mask`/`stop_multi_color_mask` in `ops.py`,
invoked via `bpy.ops` rather than a keymap of its own -- see
docs/domains/overlay_color.md. Now that both triggers are gone, that mask
trio has no keymap and no automatic caller at all, reachable only via
manual `bpy.ops` or the Python console. As of 2026-09-11 that preview is
Edit Mode only (Object/Pose Mode support dropped entirely, per explicit
user request -- see `multi_color_mask_draw.py`'s docstring), so it now
shares the same Edit-Mesh-only footing this Alt+3 binding always had."""

import bpy

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return
    km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
    kmi = km.keymap_items.new(
        "superskin.toggle_multi_color", type='THREE', value='PRESS', alt=True)
    _keymaps.append((km, kmi, "Multi Color Preview"))


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
