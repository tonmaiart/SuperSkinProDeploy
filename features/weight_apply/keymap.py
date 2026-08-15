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

Alt+Ctrl+LMB / Alt+Ctrl+RMB -> the SAME gesture as Alt+LMB/Alt+RMB above
(`superskin.weight_gesture`, same fixed `action` per binding), but entered
already holding Ctrl -- `modal()`'s MOUSEMOVE branch edge-detects Ctrl on
every event (see ops.py's docstring on the Ctrl fine sub-grid), and since
`self._fine_mode` starts `False` in `invoke()`, the very first MOUSEMOVE
tick of a drag that started with Ctrl already down flips straight into
slow/fine-precision mode (0.01 grid step, `_FINE_MODE_DIVISOR` sensitivity)
with no extra code needed here or in ops.py -- the keymap entry alone is
what makes "start already in slow/fine mode" possible, by giving the user a
one-press entry point that doesn't require pressing Ctrl mid-drag first.
Reclaimed from `features/deform_bone_viewer`'s `object.mw_select_affect_boundary`
(Alt+Ctrl+RMB only -- Alt+Ctrl+LMB was previously unused) -- see that
domain's README/keymap.py for the disabled shortcut this replaced.

Shift+R -> `superskin.repeat_last_weight_apply` (ops.py), replaying the
most recently applied Add/Scale/Smooth/Sharpen action at its exact same
intensity. Bound in the same 'Mesh' keymap as the gesture shortcuts above
so it takes priority over Blender's own native "Repeat Last" (also Shift+R
by default, in the global Window keymap) while actively editing weights --
that operator's poll() fails outside that context (or before anything has
been applied yet this session), so the key event falls through to Blender's
native binding unaffected rather than being silently swallowed.
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

        # Same gesture, same fixed `action` -- entered with Ctrl already
        # held, so it starts straight in slow/fine-precision mode (see the
        # docstring above). A separate, more specific keymap entry (alt=True
        # AND ctrl=True) rather than `any`/optional modifiers on the one
        # above -- Blender only matches an unspecified modifier as "must NOT
        # be held", so the plain Alt+LMB/RMB entry above never fires while
        # Ctrl is down, and this one fires instead.
        km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
        kmi = km.keymap_items.new(
            "superskin.weight_gesture",
            type=mouse_type,
            value='PRESS',
            alt=True,
            ctrl=True,
        )
        kmi.properties.action = action
        _keymaps.append((km, kmi))

    km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
    kmi = km.keymap_items.new(
        "superskin.repeat_last_weight_apply",
        type='R',
        value='PRESS',
        shift=True,
    )
    _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
