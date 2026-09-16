"""SkinToolsUIFeature — Unified Component Architecture implementation for
the skin_tools_ui domain.

Draws the entire SKINNING-tab ("Edit weight mode UI") body itself. Two
domains are drawn through their own unchanged ``draw_section_for_tab()``:
``deform_layer_viewer`` (the primary viewer -- excluded from this migration,
see docs/domains/object_tools_ui.md's Guardrails for why) and ``tool_socket``
(drawn unwrapped, no chrome, via its own ``public_api.py`` -- see
docs/domains/tool_socket.md). Most other SKINNING-tab domains' actual
widget code has moved into a sibling ``ui_<name>.py`` module in this same
package (``ui_weight_apply.py``, ``ui_mirror.py``, ``ui_weight_info.py``,
``ui_sketch_weight.py``) -- this class's ``draw_section()`` supplies those
as the ``draw_body`` callable to
``UnifiedRegistry.draw_collapsible_section()``, while still reading each
domain's own metadata (title, locked/collapsible state, link, priority via
``get_by_tab()``'s existing sort) from its registered
``UnifiedFeatureExtension`` instance -- "the API" each domain still
provides. Each original domain keeps its own ``execute()``, PropertyGroup,
and persistence untouched.

Four domains -- ``auto_block``, ``mirror``, ``clipboard``,
``in_mesh_transfer`` -- are a further special case (2026-09-14, per explicit
user request): their action buttons no longer draw as four separate
collapsible sections at all. Instead, the first one encountered in
``get_by_tab()``'s sorted order (``auto_block``, the lowest-priority of the
four) triggers one combined draw of ``ui_action_grid.py``'s 2-column grid
(Auto Block Weight / Mirror Weight / Copy / Paste / Mark Source / Transfer,
in that order); the other three are skipped entirely when the loop reaches
them, since their buttons already rendered as part of that same grid. See
``_MERGED_GRID_DOMAIN_IDS``/``_GRID_ANCHOR_DOMAIN_ID`` below.

This is a structural-only domain with no ``draw_tab`` of its own (it is
looked up directly by ``panel_main.py`` via ``UnifiedRegistry.get_by_id()``,
not drawn through the normal per-tab loop -- if it were, it would appear in
its own iteration and recurse).

See ``docs/domains/skin_tools_ui.md`` for the full architecture note.
"""

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from ..tool_socket.public_api import draw_tool_socket_section
from . import (
    ui_weight_apply, ui_mirror, ui_weight_info, ui_sketch_weight,
    ui_action_grid,
)

_TAB_KEY = "SKINNING"

# domain_id -> the migrated ui_<name> module's draw_section(layout, context).
# A domain_id missing here falls back to its own (unmigrated)
# draw_section_for_tab() -- see draw_section() below. auto_block/mirror/
# clipboard/in_mesh_transfer are intentionally absent -- they're handled by
# _MERGED_GRID_DOMAIN_IDS instead, not through this per-domain mapping.
_DRAW_BODY_BY_DOMAIN = {
    "weight_apply": ui_weight_apply.draw_section,
    "weight_info": ui_weight_info.draw_section,
    "sketch_weight": ui_sketch_weight.draw_section,
}

# Domains whose action buttons are combined into one ui_action_grid.py
# 2-column grid instead of four separate collapsible sections (per explicit
# user request, 2026-09-14). _GRID_ANCHOR_DOMAIN_ID is whichever of these
# sorts first under get_by_tab()'s (priority, domain_id) ordering -- the
# grid draws once, at that domain's position; the rest are skipped.
_MERGED_GRID_DOMAIN_IDS = frozenset({"auto_block", "mirror", "clipboard", "in_mesh_transfer"})
_GRID_ANCHOR_DOMAIN_ID = "auto_block"


# ==============================================================================
# SkinToolsUIFeature — UnifiedFeatureExtension
# ==============================================================================

class SkinToolsUIFeature(UnifiedFeatureExtension):
    """Structural extension owning the SKINNING-tab ("Edit weight mode UI")
    section dispatch loop.

    See ``docs/domains/skin_tools_ui.md`` for the full architecture note.
    """

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "skin_tools_ui"
    actions = []
    section_title = "Skin Tools"
    draw_tab = ""  # Looked up directly by panel_main.py, not drawn via the generic tab loop.

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Draw the whole SKINNING tab: the first non-collapsible viewer
        spec (``deform_layer_viewer``, unmigrated), then every collapsible
        tool spec with separators between them (widget code supplied by
        this package's own ``ui_<name>.py`` modules where migrated), then
        ``tool_socket`` last, unwrapped.

        ``UnifiedRegistry.get_by_tab(_TAB_KEY)`` still drives ordering
        (viewer-first, then each domain's own ``priority``) exactly as
        before -- this class never hardcodes ordering, so a future
        `priority` change on any domain keeps working with no edit here.
        """
        extensions = UnifiedRegistry.get_by_tab(_TAB_KEY)

        for ext in extensions:
            if not ext.is_collapsible():
                ext.draw_section_for_tab(layout, context, _TAB_KEY)
                break

        for ext in extensions:
            if not ext.is_collapsible():
                continue
            domain_id = ext.get_id()
            if domain_id == "tool_socket":
                # No chrome -- see docs/domains/tool_socket.md. Drawn once,
                # unwrapped, after this loop (priority=9999 already
                # guarantees it sorts last).
                continue
            if domain_id in _MERGED_GRID_DOMAIN_IDS:
                if domain_id != _GRID_ANCHOR_DOMAIN_ID:
                    # Already drawn as part of the combined grid below.
                    continue
                layout.separator(factor=0.2)
                ui_action_grid.draw_section(layout, context)
                continue
            layout.separator(factor=0.2)
            UnifiedRegistry.draw_collapsible_section(
                layout, context, ext, _TAB_KEY,
                draw_body=_DRAW_BODY_BY_DOMAIN.get(domain_id),
            )

        draw_tool_socket_section(layout, context, _TAB_KEY)

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register the feature with UnifiedRegistry."""
    UnifiedRegistry.register(SkinToolsUIFeature())


def unregister():
    """Unregister the feature from UnifiedRegistry."""
    UnifiedRegistry.unregister("skin_tools_ui")
