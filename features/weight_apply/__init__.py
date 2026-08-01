"""Weight-apply domain package — Add, Scale, Smooth, Sharpen operators +
logic, plus the Weight Brush hold-to-paint gesture (`brush/` subfolder --
same domain, see `brush/__init__.py` and README.md's "Weight Brush"
section for why it isn't a separate registered feature domain)."""

from importlib import reload

from . import ui
from . import logic
from . import draw
from . import ops
from . import weight_apply_feature
from . import keymap
from .brush import BRUSH_ENABLED
from .brush import brush_logic
from .brush import brush_draw
from .brush import brush_ui
from .brush import brush_ops
from .brush import brush_tool
from .brush import brush_hover

# Deep Matrix Reload Rule: logic-bearing modules before registration
# wrappers -- brush_logic before brush_ops, matching the existing
# ui/logic/draw/ops/weight_apply_feature/keymap ordering above. Reloaded
# unconditionally regardless of BRUSH_ENABLED -- reload only re-executes
# module bodies (class/function definitions), it never registers anything
# with bpy on its own, so this stays cheap and harmless while disabled.
for mod in (ui, logic, draw, ops, weight_apply_feature, keymap,
            brush_logic, brush_draw, brush_ui, brush_ops, brush_tool, brush_hover):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    weight_apply_feature.register()
    ops.register()
    keymap.register()
    # See brush/__init__.py's BRUSH_ENABLED docstring -- temporarily
    # disabled, so no Toolbar tool, no operator, no hover cursor.
    if BRUSH_ENABLED:
        brush_ops.register()
        brush_tool.register()
        # Last -- brush_hover checks the Weight Brush tool's idname, so
        # the tool itself (brush_tool.register(), above) must already be
        # registered before the hover modal's first tick could possibly
        # run.
        brush_hover.register()


def unregister():
    if BRUSH_ENABLED:
        # First -- signals the persistent hover modal to stop on its own
        # next tick before the tool/operator it depends on (brush_tool,
        # brush_ops) get torn down below.
        brush_hover.unregister()
        brush_tool.unregister()
        brush_ops.unregister()
    keymap.unregister()
    ops.unregister()
    weight_apply_feature.unregister()
    draw.cleanup()
    brush_draw.cleanup()
