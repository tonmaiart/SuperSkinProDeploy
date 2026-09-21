"""overlay_color feature package — lifecycle and hot-reload bootstrap."""

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
