"""Keymap registration for DeformBoneViewer — owned by this feature package."""

import bpy

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

    km = kc.keymaps.new(name='Weight Paint', space_type='EMPTY')
    kmi = km.keymap_items.new(
        "superskin.toggle_mask_mode",
        type='ONE',
        value='PRESS',
        alt=True,
    )
    kmi.properties.target = 'TOGGLE'
    _keymaps.append((km, kmi, "Toggle Mask Mode"))

    # Alt+Ctrl+MMB -> object.mw_select_affect_vertices: DISABLED, reclaimed
    # by the vertex tool (see docstring above).

    # Alt+Ctrl+RMB -> object.mw_select_affect_boundary: DISABLED, reclaimed
    # by features/weight_apply (see docstring above).

    km_ui = kc.keymaps.new(name='SuperSkinPro Deform Layer Viewer', space_type='VIEW_3D', region_type='UI')

    kmi_bones = km_ui.keymap_items.new(
        "superskin.invert_vg_selection",
        type='I',
        value='PRESS',
        ctrl=True,
    )
    _keymaps.append((km_ui, kmi_bones, "Invert Bone Selection"))

    kmi_layers = km_ui.keymap_items.new(
        "superskin.layer_invert_selection",
        type='I',
        value='PRESS',
        ctrl=True,
    )
    _keymaps.append((km_ui, kmi_layers, "Invert Layer Selection"))


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    """Return the ``(km, kmi, label)`` triples registered on the addon keyconfig by
    ``register()`` above, read-only."""
    return list(_keymaps)
