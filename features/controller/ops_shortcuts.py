"""Shortcuts for SuperSkinPro — fast timeline scrub."""

import bpy

addon_keymaps = []


# ==============================================================================
# REGISTRATION
# ==============================================================================

def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon

    if kc:
        target_modes = ['Object Mode', 'Mesh', 'Pose', 'Weight Paint']
        for mode_name in target_modes:
            km = kc.keymaps.new(name=mode_name, space_type='EMPTY')
            kmi = km.keymap_items.new(
                "superskin.scrub_timeline_fast",
                type='WHEELUPMOUSE', value='PRESS', alt=True, shift=True,
            )
            kmi.properties.next = False
            addon_keymaps.append((km, kmi))

            km = kc.keymaps.new(name=mode_name, space_type='EMPTY')
            kmi = km.keymap_items.new(
                "superskin.scrub_timeline_fast",
                type='WHEELDOWNMOUSE', value='PRESS', alt=True, shift=True,
            )
            kmi.properties.next = True
            addon_keymaps.append((km, kmi))


def unregister():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
