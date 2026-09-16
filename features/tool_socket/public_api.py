"""Tool Socket domain's cross-feature public contract.

`draw_tool_socket_section` is consumed by both
`features/object_tools_ui/object_tools_ui_feature.py` (LAYER) and
`features/skin_tools_ui/skin_tools_ui_feature.py` (SKINNING) -- the one
domain whose N-panel drawing code is genuinely shared, tab-parametrized
logic (see `tool_socket_feature.py`'s own function for why it stayed there
rather than duplicating a copy per tab in each UI package). See
docs/domains/tool_socket.md and docs/domains/skin_tools_ui.md.
"""

from .tool_socket_feature import draw_tool_socket_section

__all__ = ["draw_tool_socket_section"]
