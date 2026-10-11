
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

    km = kc.keymaps.new(name='User Interface', space_type='EMPTY')
    kmi = km.keymap_items.new(
        "superskin.bone_list_invert_selection_hover",
        type='I',
        value='PRESS',
        ctrl=True,
    )
    _keymaps.append((km, kmi, "Invert Bone Selection (hover list)"))

    kmi = km.keymap_items.new(
        "superskin.list_select_all_hover",
        type='A',
        value='PRESS',
        ctrl=True,
    )
    _keymaps.append((km, kmi, "Select All Rows (hover Layer/Bone list)"))




def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    return list(_keymaps)
