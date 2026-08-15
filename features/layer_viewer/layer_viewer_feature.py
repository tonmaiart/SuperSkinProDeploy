"""LayerViewerFeature — Unified Component Architecture implementation for the layer_viewer domain.

Collapses the old LayerViewerDomain (action dispatch) and prefs.py (draw,
persistence) into a single UnifiedFeatureExtension subclass.

This is a non-collapsible viewer domain that renders the Layer List at the
top of the LAYER tab at full width.
"""

import os
import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from ...interface.utils.utils import _has_layer_system
from ...interface.utils.icons import get_edit_icon_id
from . import ui
from . import object_selector


_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# LayerViewerFeature — UnifiedFeatureExtension
# ==============================================================================

class LayerViewerFeature(UnifiedFeatureExtension):
    """Non-collapsible viewer extension for the Layer List in the LAYER tab."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "layer_viewer"
    actions = []
    section_title = "Layer List"
    draw_tab = "LAYER"
    link = "https://docs.superskinpro.com/layer_view/"
    collapsible = True
    priority = 0
    expanded_by_default = True
    locked_expanded = True
    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Render the Layer List first, at the very top of the section,
        then the Mesh selector + Enter/Exit Pose Mode button + "Edit Layer
        Weight" button (all in one row) right below it. There is no bottom
        row anymore -- initialisation happens automatically the first time
        "Edit Layer Weight" is clicked (see the button block below) and
        Clean-up lives in the list's own overflow menu (see ``ui.py``).

        No inner ``box()`` here (removed) -- drawing straight into *layout*
        so this section looks flat/consistent with the other
        ``locked_expanded`` sections (weight_apply, mirror, etc.), none of
        which wrap themselves in their own box.

        ``ui.draw_layer_list()`` is called with ``obj`` possibly ``None``
        (when ``get_effective_mesh()`` can't resolve one) -- it already
        no-ops in that case (see its own guard), so it's safe to draw
        unconditionally before the mesh selector even though the selector
        is what lets the user recover a mesh in the first place.

        The Mesh selector itself is still drawn unconditionally too -- it
        must stay usable even when the active object is an Armature (or
        nothing at all), since that's exactly the case it exists to recover
        from (selecting the rig otherwise loses SuperSkinPro's active-mesh
        detection entirely).

        The Enter/Exit Pose Mode button and the "Edit Layer Weight" button
        are both appended into that same selector row (not separate rows) --
        "Edit Layer Weight"'s own ``poll()`` already handles a ``None``
        target mesh gracefully (just stays disabled), so drawing it
        unconditionally alongside the selector is safe.

        No "No mesh active" warning is drawn anywhere in this section --
        with no resolvable mesh, the list above already renders empty and
        disabled, and every button in the selector row already reads as
        locked via its own ``poll()`` (grayed out, not clickable). That's
        enough signal on its own; a separate warning label was removed as
        redundant noise on top of an already-self-explanatory locked state.
        """
        obj = object_selector.get_effective_mesh(context)

        # Always drawn, even with no layer system yet -- an empty list
        # (rather than the whole list disappearing) keeps the panel's
        # layout/height stable regardless of init state. Also safe to call
        # with obj=None (see docstring above).
        ui.draw_layer_list(layout, context, rows=7, obj=obj)
        layout.separator(factor=0.4)

        active = context.active_object
        in_pose_mode = bool(active and active.type == 'ARMATURE' and active.mode == 'POSE')

        selector_row = object_selector.draw_object_selectors(layout, context)
        selector_row.scale_y = 1.4
        # depress=in_pose_mode gives the button a distinct pressed/highlighted
        # look while in Pose Mode, so the two states read as a clear on/off
        # toggle rather than just a text/icon swap.
        if in_pose_mode:
            selector_row.operator(
                "superskin.layer_enter_pose_mode", text="Pose", icon='ARMATURE_DATA',
                depress=True,
            )
        else:
            selector_row.operator(
                "superskin.layer_enter_pose_mode", text="Object", icon='OBJECT_DATAMODE',
                depress=False,
            )

        # Single button, single operator now -- "Edit Layer Weight"
        # (superskin.enter_layer_edit) auto-initialises the layer system
        # itself on first entry (see the Object-Mode bounce inside
        # _enter_edit_mode() in features/controller/ops_scene_modes.py)
        # instead of requiring a separate "Init" click first. Only the
        # label changes to flag that this click will also seed the mesh's
        # first Layer -- the button itself isn't locked for a mesh with no
        # Armature Modifier (poll() doesn't check for one); it stays
        # clickable and _enter_edit_mode() reports a WARNING and cancels,
        # same as clicking "Edit" on an unrigged mesh already did.
        needs_init = obj is not None and not _has_layer_system(obj)
        edit_icon_id = get_edit_icon_id()
        selector_row.operator(
            "superskin.enter_layer_edit",
            text="Init & Edit" if needs_init else "Edit",
            **({"icon_value": edit_icon_id} if edit_icon_id else {"icon": 'EDITMODE_HLT'}),
        )

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
    UnifiedRegistry.register(LayerViewerFeature())


def unregister():
    """Unregister the feature from UnifiedRegistry."""
    UnifiedRegistry.unregister("layer_viewer")
