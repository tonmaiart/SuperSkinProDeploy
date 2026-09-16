"""Weight Apply domain's cross-feature public contract.

- `force_hide_brush_hover` — consumed by
  `features/controller/ops_scene_modes.py`'s `_exit_edit_mode()` to guarantee
  the Weight Brush's hover-cursor overlay and OS-cursor override are torn
  down on every Edit Layer Weight exit path (Save Weights, Force Pose Mode,
  the auto-save guard) -- see CLAUDE.md's Viewport State Invariant, and
  `brush/brush_hover.py::force_hide()`'s own docstring for why this needs to
  be callable without holding a reference to whichever hover-modal instance
  (if any) is currently running.
- `BRUSH_ENABLED`, `ACTION_TO_GESTURE_PAIR`, `get_active_weight_tool_idname`,
  `WEIGHT_BRUSH_TOOL_IDNAME`, `draw_projection_button`, `draw_hardness_buttons`,
  `BRUSH_ROW_SCALE_Y`, `BRUSH_ICON_BUTTON_SCALE_X` — consumed by
  `features/skin_tools_ui/ui_weight_apply.py` (this domain's N-panel widget
  code, moved out per the object_tools_ui/skin_tools_ui migration -- see
  docs/domains/skin_tools_ui.md). `draw_projection_button()`,
  `draw_hardness_buttons()` and the two scale constants stay defined in
  `brush/brush_ui.py` rather than moving, since `brush/brush_tool.py`'s
  WorkSpaceTool viewport-header settings (`draw_settings()`, NOT N-panel UI)
  also depends on related primitives directly within this same package --
  only the N-panel-only composition
  (`draw_tool_select_buttons()`/`_draw_brush_settings()`) moved out.
  `draw_projection_button` was added to this export list (2026-09-15, per
  explicit user request) so `ui_weight_apply.py` could draw Projection on
  its own row instead of via `draw_icon_button_row()`'s one shared
  attached-button row — that combined function (and its singular
  `draw_hardness_button()` cycle-button half) is still used as-is by
  `brush_tool.py`'s own viewport-header settings, reached as a same-package
  import there, not through this file. `draw_hardness_buttons` (plural,
  same day, a LATER explicit user request) was added right after: three
  separate attached buttons (Hard/Medium/Soft), each dispatching the
  re-added `superskin.set_brush_hardness` operator with its own `value`,
  replacing `draw_hardness_button()` (singular, the cycle button) at THIS
  call site only — `brush_tool.py`'s viewport header keeps the single cycle
  button via `draw_icon_button_row()`/`draw_hardness_button()`, unchanged;
  the two surfaces intentionally diverge on this one control now.
  `get_active_weight_tool_idname`/`WEIGHT_BRUSH_TOOL_IDNAME` are re-exported
  under public names from `brush/brush_tool.py`'s underscore-prefixed
  originals (`_get_active_tool_idname`/`_WEIGHT_BRUSH_TOOL_IDNAME`), which
  stay private within this package.
"""

from .brush import BRUSH_ENABLED
from .brush.brush_hover import force_hide as force_hide_brush_hover
from .brush.brush_tool import (
    _get_active_tool_idname as get_active_weight_tool_idname,
    _WEIGHT_BRUSH_TOOL_IDNAME as WEIGHT_BRUSH_TOOL_IDNAME,
)
from .brush.brush_ui import (
    draw_projection_button,
    draw_hardness_buttons,
    _ROW_SCALE_Y as BRUSH_ROW_SCALE_Y,
    _ICON_BUTTON_SCALE_X as BRUSH_ICON_BUTTON_SCALE_X,
)
from .ops import ACTION_TO_GESTURE_PAIR

__all__ = [
    "force_hide_brush_hover",
    "BRUSH_ENABLED",
    "ACTION_TO_GESTURE_PAIR",
    "get_active_weight_tool_idname",
    "WEIGHT_BRUSH_TOOL_IDNAME",
    "draw_projection_button",
    "draw_hardness_buttons",
    "BRUSH_ROW_SCALE_Y",
    "BRUSH_ICON_BUTTON_SCALE_X",
]
