"""LassoToolAdjustFeature -- Unified Component Architecture for this
addon's general Vertex Selection Tools surface.

Owns the native `builtin.select_lasso` Blender tool as this addon's first
integrated Selection Tool beyond the default Box/Circle pair -- the domain
`features/weight_apply/brush/brush_ui.py`'s tool-select row switches to
when its Lasso button is clicked, replacing that row's former separate
Box/Circle buttons (see `docs/domains/weight_apply.md`'s "UI Layout"
section and `docs/domains/lasso_tool_adjust.md` for the full spec).

Deliberately a plain wrapper around a NATIVE Blender tool, not a custom
WorkSpaceTool -- the same reasoning `circle_tool_adjust` documents for
Select Circle: `builtin.select_lasso` already exists and behaves
correctly, there is nothing to reimplement.

Intended as the general home for future vertex-selection-tool
integrations (per explicit user request) -- Lasso is the first one; a
later tool gets its own action string here rather than a new domain,
unless it needs enough independent state/keymaps to warrant a package of
its own.

Also owns two drag-select gestures built directly on Blender's native
`view3d.select_circle` operator (`keymap.py`, Alt+Shift+MMB to add,
Alt+Ctrl+MMB to remove) -- added per a later explicit user request, once
Alt+Shift+MMB was freed up by `weight_apply`'s own Brush/Lasso toggle
moving to Alt+1. Ctrl+Shift+MMB was tried first for remove but collided
with Blender's native Dolly Zoom navigation -- see `keymap.py`'s own
docstring for the full mode/wait_for_input/collision rationale.

No persistent settings, no N-panel UI (`draw_tab=""`) -- Lasso has no
per-instance value to configure the way the removed `circle_tool_adjust`
domain's Select Circle radius did.
"""

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

LASSO_TOOL_IDNAME = "builtin.select_lasso"
"""Blender's native Lasso Select tool idname -- the sanctioned constant
other domains (`weight_apply`) import via `public_api.py` rather than
hardcoding the raw string themselves."""


# ==============================================================================
# LassoToolAdjustFeature -- UnifiedFeatureExtension
# ==============================================================================

class LassoToolAdjustFeature(UnifiedFeatureExtension):
    """Unified extension for the general Vertex Selection Tools domain --
    Lasso Select is the first tool it owns."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "lasso_tool_adjust"
    actions = ["activate_lasso"]
    section_title = "Lasso Select"
    draw_tab = ""  # No N-panel UI — draw_section() is never invoked by UnifiedRegistry.get_by_tab().
    keymaps = [
        {
            "key": "Alt+Shift+MMB", "label": "Drag-Select Add (Circle Brush)",
            "mode": "Hold",
        },
        {
            "key": "Alt+Ctrl+MMB", "label": "Drag-Select Remove (Circle Brush)",
            "mode": "Hold",
        },
    ]

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        if action == "activate_lasso":
            bpy.ops.superskin.lasso_tool_activate()
            return {"status": "FINISHED"}
        return {"status": "CANCELLED", "message": f"Unknown action: {action}"}

    def get_keymap_items(self) -> list:
        """Expose the drag-select Add/Remove bindings' ``(km, kmi, label)``
        triples to the in-panel shortcut editor
        (``interface/utils/keymap_editor.py``) -- see
        ``UnifiedFeatureExtension.get_keymap_items()`` for the contract."""
        from . import keymap as _keymap
        return _keymap.get_registered_keymap_items()

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """No N-panel UI — draw_tab="" keeps this out of every tab's draw
        loop, so this is never actually invoked. Required only because
        UnifiedFeatureExtension.draw_section() is abstract."""
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register the extension with UnifiedRegistry."""
    UnifiedRegistry.register(LassoToolAdjustFeature())


def unregister():
    """Unregister the extension."""
    UnifiedRegistry.unregister("lasso_tool_adjust")
