"""Shortcuts for SuperSkinPro — fast timeline scrub.

Relocated from operators/ops_shortcuts.py to features/controller/ (2026-06).

Alt+Shift+Scroll Up/Down -> `superskin.scrub_timeline_fast` (ops_tools.py) --
steps the current frame by 3 per wheel notch (plain `scene.frame_current`
stepping, not `screen.keyframe_jump` -- see ops_tools.py's docstring for why
that native operator doesn't work as a general scrub on scenes with no
keyframes). Bound across `target_modes` since it's cross-cutting timeline
navigation, not tied to any specific weight-painting mode.

The pie menu that used to live here (`MW_MT_pie_menu` / `MW_OT_call_pie`,
bound to Alt+1) was removed -- Alt+1 now toggles the shortcut-overlay HUD
instead (see `interface/utils/shortcut_overlay.py`). The individual
operators it used to invoke (`object.mw_popup_main_panel`,
`object.mw_force_pose_mode`, `superskin.save_weight_and_exit`,
`object.mirror_weights`) are untouched -- they're defined elsewhere and
were only referenced here by `bl_idname`.
"""

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
