"""Public cross-domain contract for in_mesh_transfer.

Consumed by ``features/skin_tools_ui/ui_action_grid.py`` (the combined
action grid's drawing code, per the object_tools_ui/skin_tools_ui
migration -- see docs/domains/in_mesh_transfer.md and
docs/domains/skin_tools_ui.md), which needs the marked-Source count for the
current mesh to decide whether "Transfer" is enabled and whether "Mark
Source" reads as depressed.
"""

from .logic import marked_source_count_for_mesh

__all__ = ["marked_source_count_for_mesh"]
