
import bpy

from ....interface.utils import session_shortcuts

_keymaps = []


def register():
    bound = session_shortcuts.bind("superskin.toggle_mask_mode_shortcut", type='ONE', value='PRESS', alt=True)
    if bound:
        bound[1].repeat = False
        _keymaps.append((*bound, "Toggle Edit Mask"))

    kc = bpy.context.window_manager.keyconfigs.addon
    if not kc:
        return

    km = kc.keymaps.new(name='User Interface', space_type='EMPTY')
    kmi = km.keymap_items.new(
        "superskin.bone_list_invert_selection_hover",
        type='I',
        value='PRESS',
        ctrl=True,
    )
    _keymaps.append((km, kmi, "Invert Bone Selection"))




def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    return list(_keymaps)
