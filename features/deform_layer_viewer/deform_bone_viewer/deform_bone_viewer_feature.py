"""DeformBoneViewerFeature — Unified Component Architecture implementation for the deform_bone_viewer domain.

Collapses the old DeformBoneViewerDomain (action dispatch) and prefs.py (draw,
persistence) into a single UnifiedFeatureExtension subclass.

This is a non-collapsible viewer domain that renders the Deform Bone List at the
top of the SKINNING tab at full width.
"""

import os
import bpy

from ....interface.registry.register_api import UnifiedFeatureExtension
from ....core.facade import CoreFacade
from . import ui


_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# DeformBoneViewerFeature — UnifiedFeatureExtension
# ==============================================================================

class DeformBoneViewerFeature(UnifiedFeatureExtension):
    """Non-collapsible viewer extension for the Deform Bone List in the SKINNING tab."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "deform_bone_viewer"
    actions = [
        "copy_bone_plane", "cut_bone_plane", "paste_bone_plane_add", "paste_bone_plane_subtract", "paste_bone_plane_replace",
        "copy_layer_plane", "cut_layer_plane", "paste_layer_plane_add", "paste_layer_plane_subtract", "paste_layer_plane_replace",
    ]
    section_title = "Deform Bones List"
    draw_tab = "SKINNING"
    link = "https://docs.superskinpro.com/bone_list/"
    collapsible = True
    priority = 0
    expanded_by_default = True
    locked_expanded = True
    show_section_label = False
    keymaps = [
        # "Select Affected Boundary" (Alt+Ctrl+RMB) entry temporarily
        # hidden per user request -- the shortcut itself was already
        # reclaimed by features/weight_apply for the Smooth/Sharpen
        # fine-precision gesture (see that domain's keymap.py/README), so
        # advertising it here in the shortcut-overlay HUD was stale/
        # misleading. The mw_select_affect_boundary operator remains fully
        # registered; re-adding the entry below restores the HUD line.
        # {"key": "Alt+Ctrl+RMB", "label": "Select Affected Boundary"},
        # "Toggle Mask Mode" (Alt+1) entry removed 2026-09-10, per explicit
        # user request -- the shortcut itself was reclaimed by
        # features/layer_picker (since removed entirely, leaving Alt+1
        # unclaimed again), so advertising a "Toggle Mask Mode" HUD line at
        # Alt+1 would be stale/misleading. The superskin.toggle_mask_mode operator remains
        # fully registered and reachable via the "Edit Bone"/"Edit Mask"
        # N-panel buttons; only the standalone shortcut and this HUD entry
        # are gone. Re-adding the entry below (after re-adding the keymap
        # in keymap.py on an unclaimed key) restores the HUD line.
        # {"key": "Alt+1", "label": "Toggle Mask Mode", "mode": "Toggle"},
    ]

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        """Dispatches the Deform Bones List's two Plane-Copy clipboard
        action groups ("Clipboard Bone Weight" / "Clipboard Layer
        Weight", see clipboard_logic.py and ui.py's
        SUPERSKIN_MT_bone_list_more_options / layer_viewer/ui.py's
        SUPERSKIN_MT_layer_list_more_options, where each is drawn as a
        flat set of entries, not a nested submenu) -- the only actions
        this domain owns. Paste is split into three actions (Add/
        Subtract/Replace) instead of one action plus a shared mode
        dropdown, since each is its own clickable menu entry rather than
        a persistent UI control. Everything else about this domain (the
        influence list, the Mask toggle, Save Weights & Exit) is driven
        by its own operators outside the UnifiedRegistry action-dispatch
        path."""
        from . import clipboard_logic
        try:
            if action == "copy_bone_plane":
                clipboard_logic.bone_weight_clipboard.copy(core_facade)
            elif action == "cut_bone_plane":
                clipboard_logic.bone_weight_clipboard.cut(core_facade)
            elif action == "paste_bone_plane_add":
                clipboard_logic.bone_weight_clipboard.paste(core_facade, mode='ADD')
            elif action == "paste_bone_plane_subtract":
                clipboard_logic.bone_weight_clipboard.paste(core_facade, mode='SUBTRACT')
            elif action == "paste_bone_plane_replace":
                clipboard_logic.bone_weight_clipboard.paste(core_facade, mode='REPLACE')
            elif action == "copy_layer_plane":
                clipboard_logic.layer_weight_clipboard.copy(core_facade)
            elif action == "cut_layer_plane":
                clipboard_logic.layer_weight_clipboard.cut(core_facade)
            elif action == "paste_layer_plane_add":
                clipboard_logic.layer_weight_clipboard.paste(core_facade, mode='ADD')
            elif action == "paste_layer_plane_subtract":
                clipboard_logic.layer_weight_clipboard.paste(core_facade, mode='SUBTRACT')
            elif action == "paste_layer_plane_replace":
                clipboard_logic.layer_weight_clipboard.paste(core_facade, mode='REPLACE')
            else:
                return {"status": "CANCELLED", "message": f"Unknown action: {action}"}
        except ValueError as e:
            return {"status": "CANCELLED", "message": str(e)}
        return {"status": "FINISHED"}

    def get_keymap_items(self) -> list:
        """Expose Alt+1's ``(km, kmi, label)`` to the in-panel shortcut
        editor (``interface/utils/keymap_editor.py``) -- see
        ``UnifiedFeatureExtension.get_keymap_items()`` for the contract."""
        from . import keymap as _keymap
        return _keymap.get_registered_keymap_items()

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Draw just the Deform Bones list, on its own, with no embedded
        Layer list and no bottom widget row.

        **Superseded as of the `deform_layer_viewer` merge (2026-09-10, per
        explicit user request that both lists show together, always, in
        both Object and Edit Mode) -- not called by the registered
        `DeformLayerViewerFeature`.** That parent class draws the combined
        body itself (`deform_layer_viewer_feature.py`'s `_draw_combined()`):
        the Layer list, then this Deform Bones list, then ONE shared
        widget row -- as three top-level SIBLING blocks, never one nested
        inside another's `top_fn`. This method still exists only because
        `UnifiedFeatureExtension.draw_section()` is `@abstractmethod`
        (`DeformBoneViewerFeature` must stay instantiable for its
        `execute()`/`get_keymap_items()`, still reused by the parent for
        actual dispatch) -- same "required by the ABC but not meaningfully
        used" precedent as this class's own `execute()` docstring already
        established for viewer-only domains before the merge.

        **Why the old embedded-list shape had to go, not just the
        Edit-Mode gating:** the previous version of this method embedded
        the FULL Layer list (`layer_viewer`'s own `draw_layer_list()` --
        itself a complete list-with-sidebar widget) via `top_fn` into
        THIS list's own `draw_list_with_sidebar()` call, only while
        `obj.mode == 'EDIT'`. Two problems followed directly from that
        nesting, both fixed by un-nesting rather than by tweaking the mode
        gate: (1) it made the Layer list vanish outside Edit Mode, which
        is the "หุบๆโผล่ๆ" (collapsing/appearing) behavior the user
        explicitly asked to stop; (2) `draw_list_with_sidebar()`'s side-
        button column uses a FIXED `col_btns.separator(factor=5.0)` top
        offset (`interface/template_ui/layout.py`) calibrated for a
        `top_fn` that draws one simple row (a Mesh dropdown or a plain
        Label) -- nesting an entire second list-with-sidebar widget inside
        it made this list's own filter/clipboard side buttons render at
        the wrong vertical offset, no longer aligned with this list's own
        rows. That misalignment (not a broken icon or a missing asset) is
        what read as "หน้าตาพังๆ" in the screenshot reporting this. Kept
        as siblings, each `draw_list_with_sidebar()` call's `top_fn` is
        back to a single simple row (or none), so each list's own side
        buttons line up correctly again.
        """
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            layout.label(text="No mesh active", icon='ERROR')
            return

        ui.draw_influence_list_system(layout, context, rows=7)

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


# ==============================================================================
# Registration
# ==============================================================================
#
# This class is no longer registered with UnifiedRegistry directly -- it and
# its `layer_viewer` counterpart were merged into one domain,
# `deform_layer_viewer`, registered under both the LAYER and SKINNING tabs
# (see docs/domains/deform_layer_viewer.md). `deform_layer_viewer_feature.py`
# (the package parent) instantiates this class and delegates execute()/
# draw_section() to it for the SKINNING tab; no `register()`/`unregister()`
# function is needed here anymore.
