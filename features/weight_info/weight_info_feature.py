"""WeightInfoFeature — Unified Component Architecture implementation for the
weight_info domain.

Read-only viewer: while exactly one vertex is selected, lists that vertex's
non-zero active-layer bone weights — one row per vertex group (row = bone
name, value = weight). Any other selection count (zero, or more than one)
clears the list entirely, since a multi-vertex summary was never the intent
of this domain. Mask data and other layers are never read — only the
active layer's weight dict (``CoreFacade.read_active_layer()``), which is
already mode-aware (Edit Mode temp VGs vs. Object Mode ``ss_layer_N``).

Owns:
  - Action dispatch: none (viewer-only, execute() is a permanent no-op)
  - JSON persistence: no-op (no persistent settings)

UI layout — the ``SSPrefWeightInfoEntry``/``SSPrefWeightInfo`` PropertyGroups,
``SUPERSKIN_UL_weight_info_list`` UIList, and ``draw_section()`` body — moved
to ``features/skin_tools_ui/ui_weight_info.py`` (2026-09-14, see
docs/domains/skin_tools_ui.md). They were pure display scratch state
(rebuilt every draw, never persisted), so they moved together with the
drawing code rather than staying behind as otherwise-unused registrations
here.
"""

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade


# ==============================================================================
# WeightInfoFeature — UnifiedFeatureExtension
# ==============================================================================

class WeightInfoFeature(UnifiedFeatureExtension):
    """Viewer extension listing one vertex's active-layer weights, per
    vertex group, in the SKINNING tab."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "weight_info"
    actions = []
    section_title = "Weight Info"
    draw_tab = "SKINNING"
    collapsible = True
    priority = 4
    expanded_by_default = True
    locked_expanded = True

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        # Viewer-only domain — no dispatchable actions.
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """UI moved to features/skin_tools_ui/ui_weight_info.py — see
        docs/domains/skin_tools_ui.md."""
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
    UnifiedRegistry.register(WeightInfoFeature())


def unregister():
    """Unregister the extension."""
    UnifiedRegistry.unregister("weight_info")
