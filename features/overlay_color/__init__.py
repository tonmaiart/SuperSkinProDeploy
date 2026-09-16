"""overlay_color feature package — lifecycle and hot-reload bootstrap.

Merges the former `vgcolor` (weight/mask ramp customization) and
`multi_color_preview` (Alt+3 rainbow per-bone preview) domains into one —
see `overlay_color_feature.py`'s module docstring for why. Owns:

  - Two hardcoded ramps (`native_sync.py`'s `_EDIT_RAMP_STOPS` /
    `_MASK_RAMP_STOPS`) — not user-editable, no settings UI.
  - `native_sync.py`'s auto lifecycle watcher that pushes the active ramp
    into Blender's native weight-color pipeline while "Edit Layer Weight"
    is active.
  - `multi_color_draw.py`'s Alt+3 toggle (`ops.py` + `keymap.py`) that
    blends per-bone colors into a temp mesh color attribute + native
    Solid/Attribute shading.
  - `multi_color_mask_draw.py`, the Edit Mask sub-mode counterpart (Edit
    Mode only, no keymap and no automatic trigger of its own -- it used to
    be auto-activated by the layer_picker domain's Alt+1, since removed
    entirely per explicit user request) that blends every visible Layer's
    own color (weighted by that Layer's mask) instead of per-bone weights.

Neither overlay mode ever uses a custom GPU shader draw handler for mesh
color — see each module's own docstring.
"""

from importlib import reload

from . import _ramp_io
from . import multi_color_draw
from . import multi_color_mask_draw
from . import native_sync
from . import overlay_color_feature
from . import ops
from . import keymap

for mod in (_ramp_io, multi_color_draw, multi_color_mask_draw, native_sync,
            overlay_color_feature, ops, keymap):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    overlay_color_feature.register()
    ops.register()
    keymap.register()
    native_sync.register()
    multi_color_draw.register()
    multi_color_mask_draw.register()


def unregister():
    multi_color_mask_draw.unregister()
    multi_color_draw.unregister()
    native_sync.unregister()
    keymap.unregister()
    ops.unregister()
    overlay_color_feature.unregister()
