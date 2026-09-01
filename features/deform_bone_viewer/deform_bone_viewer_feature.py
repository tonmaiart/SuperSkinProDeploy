"""DeformBoneViewerFeature — Unified Component Architecture implementation for the deform_bone_viewer domain.

Collapses the old DeformBoneViewerDomain (action dispatch) and prefs.py (draw,
persistence) into a single UnifiedFeatureExtension subclass.

This is a non-collapsible viewer domain that renders the Deform Bone List at the
top of the SKINNING tab at full width.
"""

import os
import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from ...interface.utils.icons import get_layer_mask_icon_id
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
        "copy_bone_plane", "paste_bone_plane_add", "paste_bone_plane_subtract", "paste_bone_plane_replace",
        "copy_layer_plane", "paste_layer_plane_add", "paste_layer_plane_subtract", "paste_layer_plane_replace",
    ]
    section_title = "Deform Bones List"
    draw_tab = "SKINNING"
    link = "https://docs.superskinpro.com/bone_list/"
    collapsible = True
    priority = 0
    expanded_by_default = True
    locked_expanded = True
    keymaps = [
        # "Select Affected Boundary" (Alt+Ctrl+RMB) entry temporarily
        # hidden per user request -- the shortcut itself was already
        # reclaimed by features/weight_apply for the Smooth/Sharpen
        # fine-precision gesture (see that domain's keymap.py/README), so
        # advertising it here in the shortcut-overlay HUD was stale/
        # misleading. The mw_select_affect_boundary operator remains fully
        # registered; re-adding the entry below restores the HUD line.
        # {"key": "Alt+Ctrl+RMB", "label": "Select Affected Boundary"},
        {"key": "Alt+1", "label": "Toggle Mask Mode", "mode": "Toggle"},
    ]

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        """Dispatches the Deform Bones List's two Plane-Copy clipboard
        drop-down menus ("Clipboard Bone Weight" / "Clipboard Layer
        Weight", see clipboard_logic.py and ui.py's
        SUPERSKIN_MT_deform_bone_weight_clipboard /
        SUPERSKIN_MT_deform_layer_weight_clipboard) -- the only actions
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
            elif action == "paste_bone_plane_add":
                clipboard_logic.bone_weight_clipboard.paste(core_facade, mode='ADD')
            elif action == "paste_bone_plane_subtract":
                clipboard_logic.bone_weight_clipboard.paste(core_facade, mode='SUBTRACT')
            elif action == "paste_bone_plane_replace":
                clipboard_logic.bone_weight_clipboard.paste(core_facade, mode='REPLACE')
            elif action == "copy_layer_plane":
                clipboard_logic.layer_weight_clipboard.copy(core_facade)
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
        """Render the Deform Bone List, then a bottom row with the "Mask"
        toggle button and the Save Weights & Exit button.

        The "Clipboard Bone Weight" / "Clipboard Layer Weight" drop-down
        menus are NOT drawn here -- they live in the list's own sidebar
        button column (ui.py's draw_influence_list_system(), next to the
        existing "More" overflow menu), not in this section's body.

        No inner ``box()`` here (removed) -- drawing straight into *layout*
        so this section looks flat/consistent with the other
        ``locked_expanded`` sections (weight_apply, mirror, etc.), none of
        which wrap themselves in their own box.

        No standalone layer-name label anymore -- the "Mask" toggle button
        (``superskin.toggle_mask_mode``) now doubles as that label, drawn
        with the active layer's own text and the custom edit-mask icon
        (``interface/utils/icons.py::get_layer_mask_icon_id()``, falling
        back to the built-in ``RENDERLAYERS`` icon if that asset failed to
        load) instead of a fixed "Mask" caption. Its pressed (``depress``)
        state still communicates whether Mask edit mode is currently
        active.
        """
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            layout.label(text="No mesh active", icon='ERROR')
            return

        layer_name = "—"
        if "ss_layers_meta" in obj.data:
            try:
                layer_name = CoreFacade(context).active_layer_name()
            except ValueError:
                pass  # Not activated -- shouldn't reach here, but stay graceful.

        ui.draw_influence_list_system(layout, context, rows=7)
        layout.separator(factor=0.4)
        row = layout.row()
        row.scale_y = 1.4

        mask_icon_id = get_layer_mask_icon_id()
        row.operator(
            "superskin.toggle_mask_mode", text=layer_name,
            depress=obj.superskin_storage.active_is_mask,
            **({"icon_value": mask_icon_id} if mask_icon_id else {"icon": 'RENDERLAYERS'}),
        )
        row.separator(factor=2.0)
        row.operator("superskin.save_weight_and_exit", icon='IMPORT')

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
    UnifiedRegistry.register(DeformBoneViewerFeature())


def unregister():
    """Unregister the feature from UnifiedRegistry."""
    UnifiedRegistry.unregister("deform_bone_viewer")
