"""Weight Transfer domain's cross-feature public contract.

`sync_active_entry_from_viewport` is consumed by
`features/object_tools_ui/ui_weight_transfer.py` (this domain's UI code,
moved out per the object_tools_ui/skin_tools_ui migration -- see
docs/domains/skin_tools_ui.md), which needs to run the reverse
list<->viewport selection sync once per redraw of the Transfer tab.
"""

from .state_ops import sync_active_entry_from_viewport

__all__ = ["sync_active_entry_from_viewport"]
