"""layer_viewer's cross-domain contract (CLAUDE.md's "Controlled
Cross-Domain Imports"). Nothing outside this file is a valid import target
for another feature domain -- see docs/domains/deform_layer_viewer.md's
"Public API Surface" section for the full list of exports/consumers.
"""

from .ui import draw_layer_list
from .object_selector import get_effective_mesh, get_effective_armature

__all__ = ["draw_layer_list", "get_effective_mesh", "get_effective_armature"]
