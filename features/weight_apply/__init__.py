"""Weight-apply domain package — Add, Scale, Smooth, Sharpen operators +
logic, plus the `brush_tool` and `vertex_tool` subpackages."""

from importlib import import_module, reload

from . import logic
from . import draw
from . import ops
from . import weight_apply_feature
from . import keymap
brush = import_module(f"{__name__}.brush_tool")
from .brush_tool import (
    brush_logic, brush_draw, brush_prep, brush_ops, brush_hover,
    brush_radius_adjust, brush_hardness_adjust, brush_tool, brush_ui,
)
from .brush_tool import keymap as brush_keymap
from .vertex_tool import (
    common as vertex_common, select_tool, loop_select, lasso_ops,
    grow_shrink_ops, pick, select_draw, select_ops, lasso_select, keymap as vertex_keymap,
)

# Deep Matrix Reload Rule: logic-bearing modules before registration
# wrappers.
for mod in (vertex_common, pick, logic, draw, ops, weight_apply_feature, keymap,
            brush, brush_logic, brush_draw, brush_ui, brush_prep, brush_ops, brush_hover,
            brush_radius_adjust, brush_hardness_adjust, brush_tool, brush_keymap,
            select_tool, loop_select, lasso_ops, grow_shrink_ops, select_draw, select_ops, lasso_select, vertex_keymap):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    weight_apply_feature.register()
    ops.register()
    keymap.register()
    draw.register()
    lasso_ops.register()
    select_tool.register()
    loop_select.register()
    grow_shrink_ops.register()
    select_draw.register()
    select_ops.register()
    lasso_select.register()
    vertex_keymap.register()
    if brush.BRUSH_ENABLED:
        brush_prep.register()
        brush_ops.register()
        brush_hover.register()
        brush_keymap.register()
        brush_radius_adjust.register()
        brush_hardness_adjust.register()
        brush_tool.register()


def unregister():
    if brush.BRUSH_ENABLED:
        brush_tool.unregister()
        brush_hardness_adjust.unregister()
        brush_radius_adjust.unregister()
        brush_keymap.unregister()
        brush_hover.unregister()
        brush_ops.unregister()
        brush_prep.unregister()
        brush_draw.cleanup()
    vertex_keymap.unregister()
    lasso_select.unregister()
    select_ops.unregister()
    select_draw.unregister()
    grow_shrink_ops.unregister()
    loop_select.unregister()
    select_tool.unregister()
    lasso_ops.unregister()
    keymap.unregister()
    ops.unregister()
    weight_apply_feature.unregister()
    draw.cleanup()
