"""Public cross-feature surface for lasso_tool_adjust.

Exposes the domain's native tool idname constant so another feature
package can switch to Lasso Select without reaching into
`lasso_tool_adjust_feature.py` directly -- see this project's "Controlled
Cross-Domain Imports" rule in CLAUDE.md. Consumed by
`features/weight_apply/brush/brush_tool.py` (inside
`SUPERSKIN_OT_toggle_weight_brush_tool.execute()`, shared by its Alt+1
keymap and the N-panel's single Brush/Lasso cycle button) so it doesn't
hardcode the raw native idname string itself (see
`docs/domains/weight_apply.md`'s "UI Layout" section).
"""

from .lasso_tool_adjust_feature import LASSO_TOOL_IDNAME

__all__ = ["LASSO_TOOL_IDNAME"]
