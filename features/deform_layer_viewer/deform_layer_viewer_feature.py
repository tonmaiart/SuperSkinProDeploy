"""DeformLayerViewerFeature — the single UnifiedFeatureExtension for the merged
`deform_layer_viewer` domain."""

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...interface.utils.utils import _has_layer_system
from ...interface.utils.mode_edit_toggle import draw_mode_edit_toggle, draw_edit_mask_button
from .layer_viewer import object_selector
from .layer_viewer.public_api import draw_layer_list
from .deform_bone_viewer.ui import draw_influence_list_system
from .deform_bone_viewer.deform_bone_viewer_feature import DeformBoneViewerFeature


# ==============================================================================
# DeformLayerViewerFeature — UnifiedFeatureExtension
# ==============================================================================

class DeformLayerViewerFeature(UnifiedFeatureExtension):
    """Non-collapsible viewer extension rendering the Layer List and the Deform Bones List
    together."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "deform_layer_viewer"
    # Only the deform_bone_viewer half owns real dispatchable actions (the
    # ten Clipboard Bone/Layer Weight menu entries).
    actions = DeformBoneViewerFeature.actions
    draw_tab = ("LAYER", "SKINNING")
    collapsible = True
    priority = 0
    expanded_by_default = True
    locked_expanded = True
    show_section_label = False
    keymaps = DeformBoneViewerFeature.keymaps

    # ── Construction ──────────────────────────────────────────────────────

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._bone_ext = DeformBoneViewerFeature()

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade) -> dict:
        return self._bone_ext.execute(action, context, core_facade)

    def get_keymap_items(self) -> list:
        return self._bone_ext.get_keymap_items()

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section_for_tab(self, layout, context, tab_key: str) -> None:
        self._draw_combined(layout, context, tab_key)

    def draw_section(self, layout, context) -> None:
        self._draw_combined(layout, context, "LAYER")

    def _draw_combined(self, layout, context, tab_key: str) -> None:
        """Top row: the "[Object/Pose/Edit Bone]" cycle button, the "bind mesh" dropdown, and
        the "Settings"/"How to."""
        obj = object_selector.get_effective_mesh(context)
        is_edit = obj is not None and obj.mode == 'WEIGHT_PAINT'
        needs_init = obj is not None and not _has_layer_system(obj)

        top_row = layout.row(align=True)
        outer_split = top_row.split(factor=0.45, align=True)
        cycle_zone = outer_split.row(align=True)
        cycle_zone.scale_y = 1.4
        draw_mode_edit_toggle(
            cycle_zone, context,
            pose_toggle_idname="superskin.layer_enter_pose_mode",
            enter_edit_idname="superskin.enter_layer_edit",
            enter_edit_text="Init & Weight Paint" if needs_init else "Weight Paint",
            edit_mask_idname="superskin.toggle_mask_mode",
            edit_mask_text="Edit Mask",
            edit_mask_enter_text="Init & Edit Mask" if needs_init else "Edit Mask",
            # "Edit Mask" is still drawn as its own separate button below.
            show_edit_mask=False,
            target_mesh_obj=obj,
        )

        rest_zone = outer_split.row(align=True)
        mesh_zone = rest_zone.row(align=True)
        if is_edit:
            mesh_zone.enabled = False
        object_selector.draw_object_selectors(mesh_zone, context)

        settings_zone = rest_zone.row(align=True)
        UnifiedRegistry.draw_settings_toggle_row(settings_zone, context)

        layout.separator(factor=0.5)

        draw_layer_list(layout.column(), context=context, rows=6, obj=obj)
        draw_influence_list_system(layout.column(), context=context, rows=7, obj=obj)

        if tab_key != "SKINNING":
            row = layout.row()
            row.scale_y = 1.4
            mask_zone = row.row()
            mask_zone.alignment = 'RIGHT'
            draw_edit_mask_button(
                mask_zone, context,
                enter_edit_idname="superskin.enter_layer_edit",
                edit_mask_idname="superskin.toggle_mask_mode",
                edit_mask_text="Mask",
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
    """Register the merged feature with UnifiedRegistry."""
    UnifiedRegistry.register(DeformLayerViewerFeature())


def unregister():
    """Unregister the merged feature from UnifiedRegistry."""
    UnifiedRegistry.unregister("deform_layer_viewer")
