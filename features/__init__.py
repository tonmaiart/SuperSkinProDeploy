"""SuperSkinPro — Extra Domains Package Initializer.

This module acts as the central registry gateway for all feature domains.
Every domain folder under features/ must register its lifecycle here.

As of the Unified Component Architecture refactor, each feature domain
owns a ``*_feature.py`` file containing a ``UnifiedFeatureExtension``
subclass. The domain's ``__init__.py`` calls ``<name>_feature.register()``
which registers with ``UnifiedRegistry`` (and legacy registries for
backward compatibility).

Registration order controls insertion order within each tab.
The viewer domains must be first so they render at the top of their tabs:

  LAYER tab   : layer_viewer (first, non-collapsible) → weight_transfer (owns Export/Import JSON too)
  SKINNING tab: deform_bone_viewer (first, non-collapsible) → weight_apply → auto_block → mirror → weight_info → clipboard → circle_tool_adjust → in_mesh_transfer
  PREFERENCE  : bone_picker → overlay_color → debug_console (hosted under SYSTEM tab)

``addon_updater`` is also a PREFERENCE-tab domain, but its actual UI placement
is a special case: interface/panel_main.py's preference section calls its
draw_section() directly (looked up via UnifiedRegistry.get_by_id), before it
ever calls widget_preferences.draw_preferences_body() -- so it always renders
first in the "Preference" section regardless of this tuple's ordering or the
domain's priority. See features/addon_updater/README.md's "Section Placement" section.
It is registered early in this tuple only so that, consistent with the
project's general "fail early, fail visibly" preference, an error in a later
domain's register() doesn't prevent the updater's own operators from being
registered first.
"""

from importlib import reload

# Viewer domains — must register before any other spec in their tabs
from . import layer_viewer         # LAYER tab — first (non-collapsible)
from . import deform_bone_viewer   # SKINNING tab — first (non-collapsible)

# Standard feature domains
from . import addon_updater        # PREFERENCE tab (drawn directly by panel_main.py's preference section, see docstring above)
from . import activate             # PREFERENCE tab (also drawn directly by panel_main.py's preference section, see features/activate/README.md)
from . import weight_apply
from . import auto_block_weight
from . import mirror
# from . import weight_info          # TEMPORARILY DISABLED -- SKINNING tab -- read-only selected-vertex active-layer weight viewer
from . import clipboard
from . import bone_picker
from . import weight_transfer      # LAYER tab (owns Export/Import JSON too)
from . import overlay_color        # PREFERENCE tab -- weight/mask edit color ramps + Alt+3 Multi Color Preview, see features/overlay_color/README.md (merged from the former vgcolor + multi_color_preview domains)
from . import circle_tool_adjust   # also owns Grow/Shrink Selection (Alt+Shift+Scroll), merged in from the former auto_grow domain
from . import in_mesh_transfer     # SKINNING tab -- Mark Source / Transfer within a single mesh's active Layer
from . import debug_console

# Cross-cutting domains with no N-panel UI (no draw_tab)
from . import pick_walk        # Alt+Ctrl+MMB hold+drag -- Maya-style pick walk, see features/pick_walk/README.md
from . import controller       # scene-mode gate, pie menu, safe-shrink

_modules = (
    layer_viewer,
    deform_bone_viewer,
    addon_updater,
    activate,
    weight_apply,
    auto_block_weight,
    mirror,
    # weight_info,  # TEMPORARILY DISABLED
    clipboard,
    bone_picker,
    weight_transfer,
    overlay_color,
    circle_tool_adjust,
    in_mesh_transfer,
    debug_console,
    pick_walk,
    controller,
)

for mod in _modules:
    try:
        reload(mod)
    except Exception:
        pass


def register():
    """Register all extra domain lifecycles sequentially."""
    for mod in _modules:
        if hasattr(mod, "register"):
            mod.register()


def unregister():
    """Unregister all extra domain lifecycles in reverse order."""
    for mod in reversed(_modules):
        if hasattr(mod, "unregister"):
            mod.unregister()
