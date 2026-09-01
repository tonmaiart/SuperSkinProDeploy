"""Sketch Weight Guide -- see docs/domains/sketch_weight.md."""

from importlib import reload
from . import logic, draw, ops, tool, sketch_weight_feature

# Bottom-up reload -- foundations before wrappers
for mod in (logic, draw, ops, tool, sketch_weight_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    sketch_weight_feature.register()
    ops.register()
    tool.register()


def unregister():
    tool.unregister()
    ops.unregister()
    sketch_weight_feature.unregister()
    draw.cleanup()
