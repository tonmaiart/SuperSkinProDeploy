"""Keymap registration for the Weight Apply gesture shortcuts — owned by
this feature package.

Shortcuts (Mesh Edit Mode), both bound to the same modal operator
(`superskin.weight_gesture`, ops.py) with a fixed `action` per binding --
there is no mid-gesture mode switch (a previous revision had a Ctrl-tap
toggle between the two; removed):

  Alt+LMB  -> "add_scale" mode (Add on positive drag, Scale on negative)
  Alt+RMB  -> "smooth_sharpen" mode (Smooth on positive drag, Sharpen
              on negative)

During either gesture, scrolling steps through a 3-tier slow-down
(normal -> 3x slower -> 6x slower, scroll down to slow further, scroll up
to speed back toward normal) -- see ops.py:SUPERSKIN_OT_weight_gesture for
the full contract.

`smooth_sharpen`'s binding has moved a few times: a separate Alt+RMB
binding, then a Ctrl-tap mode switch on the Alt+LMB gesture, then a direct
Alt+Shift+LMB entry point, now back to plain Alt+RMB. `circle_tool_adjust`
uses Alt+Shift+RMB (with Shift), so this doesn't collide with it.
"""

import bpy

_keymaps = []

_GESTURE_BINDINGS = (
    ("add_scale", 'LEFTMOUSE'),
    ("smooth_sharpen", 'RIGHTMOUSE'),
)


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return
    for action, mouse_type in _GESTURE_BINDINGS:
        km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
        kmi = km.keymap_items.new(
            "superskin.weight_gesture",
            type=mouse_type,
            value='PRESS',
            alt=True,
        )
        kmi.properties.action = action
        _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
