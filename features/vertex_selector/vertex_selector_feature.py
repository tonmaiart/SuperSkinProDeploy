"""VertexSelectorFeature — Unified Component Architecture for selection-
mutating gestures: Grow/Shrink Selection (hop-count only).

Merged from the former `features/circle_tool_adjust/` (just the Grow/Shrink
half -- the brush-radius/tool-toggle gesture stayed behind, it configures a
tool rather than mutating a selection).

Pick Walk and the geodesic-distance Grow/Shrink mode (formerly also owned
here) have been removed entirely, along with the `SSPrefVertexSelector`
PropertyGroup that only ever backed those two (Pick Walk's `step_pixels`
hop sensitivity, distance mode's `distance_grow_factor`) -- this domain now
has no settings of its own.

Owns:
  - No dispatch actions -- `superskin.grow_shrink_selection` is purely
    keymap-triggered (see ops.py), same pattern as `circle_tool_adjust`'s
    own Grow/Shrink binding before this merge.

No N-panel UI (draw_tab="") -- neither predecessor had one.
"""

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade


# ==============================================================================
# VertexSelectorFeature — UnifiedFeatureExtension
# ==============================================================================

class VertexSelectorFeature(UnifiedFeatureExtension):
    """Structural extension for the VertexSelector domain -- no N-panel UI,
    purely keymap-triggered selection-mutation gestures."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "vertex_selector"
    actions = []
    section_title = "Vertex Selector"
    draw_tab = ""  # No N-panel UI — draw_section() is never invoked by UnifiedRegistry.get_by_tab().
    keymaps = [
        {"key": "Alt+Ctrl+Scroll", "label": "Grow/Shrink Selection"},
    ]

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED", "message": f"Unknown action: {action}"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """No N-panel UI — draw_tab="" keeps this out of every tab's draw
        loop, so this is never actually invoked. Required only because
        UnifiedFeatureExtension.draw_section() is abstract."""
        pass

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register the extension with UnifiedRegistry."""
    UnifiedRegistry.register(VertexSelectorFeature())


def unregister():
    """Unregister the extension."""
    UnifiedRegistry.unregister("vertex_selector")
