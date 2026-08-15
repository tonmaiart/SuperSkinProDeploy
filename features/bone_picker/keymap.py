"""Bone Picker keymap registration — owned by the bone_picker feature package.

Shortcuts:
  Alt+2            → invoke bone picker modal (stays open until explicitly cancelled)
  Alt+3            → toggle color bone style

Inside the modal:
  Left click / drag  → sweep add to multi selection
  Right click on bone → remove bone from multi selection
  Right click empty  → cancel / revert
  Release 2          → confirm single select (hovered bone), exit
  ESC                → cancel / revert

Note: there used to also be an Alt+2+Scroll shortcut
(superskin.bone_overlay_size_step) for nudging the static overlay's
overall_size. Removed -- its Blender-native "Adjust Last Operation" redo
panel got clobbered by object.mw_pick_bone's own undo push on every
release, so it was never reliable; the Bone Picker Settings UI section
(an Overall Size slider, bone_picker_feature.py's draw_section()) is the
only way to adjust it now.
"""

import bpy

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

    km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
    kmi = km.keymap_items.new("object.mw_pick_bone", type='TWO', value='PRESS', alt=True)
    _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
