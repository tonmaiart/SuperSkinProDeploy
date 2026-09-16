"""VertexSelector feature package — lifecycle and hot-reload bootstrap."""

from importlib import reload

from . import vertex_selector_feature
from . import ops
from . import keymap

for mod in (vertex_selector_feature, ops, keymap):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    vertex_selector_feature.register()
    ops.register()
    keymap.register()


def unregister():
    keymap.unregister()
    ops.unregister()
    vertex_selector_feature.unregister()
