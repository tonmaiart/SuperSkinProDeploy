"""Weight Brush -- hover-preview keymap.

Registers `superskin.weight_brush_hover`'s `MOUSEMOVE`/`ANY` binding as a
plain addon keymap on the "Mesh" keymap (`space_type='EMPTY'`) -- the same
convention every other custom keymap in this codebase already uses
(`bone_picker/keymap.py`, `overlay_color/keymap.py`,
`weight_apply/keymap.py`'s own Alt-drag gesture) -- deliberately NOT an
entry in `brush_tool.py`'s own `bl_keymap`
(a `WorkSpaceTool`-owned tool keymap), which is where this binding used to
live.

That split is load-bearing, not stylistic: see `docs/bug-history/0038` for
the full diagnosis, but the short version is that binding a bare
`MOUSEMOVE`/`ANY` entry inside a `WorkSpaceTool`'s own `bl_keymap` --
independent of anything `SUPERSKIN_OT_weight_brush_hover` itself does with
those events, which was confirmed correct (`PASS_THROUGH` everywhere it
should be, no lingering modal) -- silently broke ALL panel-content
interaction in the 3D View editor (every N-panel tab's own widgets, not
just this addon's; tab-switching itself kept working) for as long as the
Weight Brush tool stayed the active tool, the moment that keymap entry
fired even once. This is apparently a genuine Blender engine behavior tied
to a *tool's own* keymap reacting to `MOUSEMOVE`, not anything reachable
from operator-level code -- confirmed by process of elimination (a broad
codebase search found no addon-side state that could explain it) and by
direct experiment (removing only this one keymap binding, with everything
else unchanged, fixed the symptom). Nothing else in this whole addon binds
`MOUSEMOVE` to a `WorkSpaceTool`'s own `bl_keymap`; every other
hover/drag-tracking operator here (including this one, now) uses a plain
mode-level addon keymap instead.

**Consequence this file's registration must account for:** a `'Mesh'`-mode
keymap applies throughout Edit Mesh mode regardless of which editor/area
the mouse is currently over -- unlike the old tool keymap, which was
naturally scoped to the 3D viewport by construction. `SUPERSKIN_OT_
weight_brush_hover.invoke()` (`brush_hover.py`) therefore explicitly checks
`_is_weight_brush_tool_active()`, `context.area.type == 'VIEW_3D'`, and the
viewport's own `'WINDOW'` region before doing anything -- see that
operator's own docstring.
"""

import bpy

_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc is None:
        return
    km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
    kmi = km.keymap_items.new("superskin.weight_brush_hover", type='MOUSEMOVE', value='ANY')
    _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
