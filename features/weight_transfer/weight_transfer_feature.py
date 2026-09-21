"""WeightTransferFeature / WeightExportFeature / WeightImportFeature."""

import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import state_ops

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# Property Groups
# ==============================================================================

def _on_changed(self, context):
    from ...core.facade import CoreFacade
    CoreFacade.save_prefs()


def _poll_mesh_object(self, obj):
    return obj.type == 'MESH'


def _on_is_source_changed(self, context):
    """Enforce "only one entry may be the Source at a time"."""
    if not self.is_source:
        return
    state = context.scene.superskin_weight_transfer_state
    for entry in state.entries:
        if entry != self and entry.is_source:
            entry.is_source = False


def _on_active_entry_changed(self, context):
    """Sync the 3D viewport selection to whichever row is now active in the unified
    Source/Target list."""
    if state_ops.is_syncing_viewport_selection():
        return
    state = self
    if not (0 <= state.active_entry_index < len(state.entries)):
        return
    obj = state.entries[state.active_entry_index].object
    if obj is None or obj.name not in context.view_layer.objects:
        return
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


class SSWeightTransferEntryItem(bpy.types.PropertyGroup):
    """One row in the unified Source/Target list."""
    object: bpy.props.PointerProperty(
        type=bpy.types.Object,
        name="Mesh",
        poll=_poll_mesh_object,
    )
    use_selected_verts: bpy.props.BoolProperty(
        name="Use Selected Vertices Only",
        description="Restrict this mesh's contribution to the transfer to only its currently selected vertices, instead of the whole mesh",
        default=False,
    )
    is_source: bpy.props.BoolProperty(
        name="Is Source",
        description="Mark this mesh as the Source for the transfer — only one entry in the list may be the Source at a time",
        default=False,
        update=_on_is_source_changed,
    )


class SSWeightTransferState(bpy.types.PropertyGroup):
    """Live/working Source + Target configuration for object.mw_copy_skin_weight_maya,
    registered on bpy.types.Scene."""
    entries: bpy.props.CollectionProperty(type=SSWeightTransferEntryItem)
    active_entry_index: bpy.props.IntProperty(default=0, update=_on_active_entry_changed)


class SSPrefWeightTransfer(bpy.types.PropertyGroup):
    """Weight Transfer settings (per-machine."""
    keep_old_layer_data: bpy.props.BoolProperty(
        name="Keep Old Layer Data",
        description="Keep the target's existing vertex groups and add the transferred ones on top, instead of clearing them first",
        default=False,
        update=_on_changed,
    )
    transfer_method: bpy.props.EnumProperty(
        name="Method",
        items=[
            ('CLOSEST_DISTANCE', "Closest Distance", "Blend weights along the source's axial bone chain based on world position"),
            ('VERTEX_ID', "Vertex ID", "Copy weights using matching vertex indices (source and target must have identical vertex counts)"),
        ],
        default='CLOSEST_DISTANCE',
        update=_on_changed,
    )


# ==============================================================================
# WeightTransferFeature / WeightExportFeature / WeightImportFeature
# — UnifiedFeatureExtension, three separate always-visible LAYER-tab sections
# ==============================================================================
#
# Split 2026-08-07 from a single "Weight Transfer" dropdown entry containing
# an inline Transfer/Export/Import tab bar into three independently-selectable
# entries (per explicit user request), so each shows up as its own section.
# All three render as normal always-visible LAYER-tab sections. All three still live in this one
# package/file — they share transfer_core/ops.py/io_ops.py/state_ops.py and
# the SSPrefWeightTransfer PropertyGroup above — only the UI entry points were
# split, which places no restriction on more than one such subclass living in
# the same feature package. Only WeightTransferFeature keeps defaults_path/populate/
# serialize_into — Export and Import have no settings of their own beyond
# what's already read from the shared prefs group, so a second/third
# default_config.json entry point would be redundant.

class WeightTransferFeature(UnifiedFeatureExtension):
    """Unified extension for the Weight Transfer domain (the live mesh-to-mesh transfer, via
    the unified Source/Target list."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "weight_transfer"
    actions = ["transfer_weight_maya"]
    section_title = "Weight Transfer"
    draw_tab = "LAYER"
    link = "https://docs.superskinpro.com/transfer_weight/"
    defaults_path = _DEFAULTS_PATH
    locked_expanded = True

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        core_facade.debug_log("feature_domains", f"weight_transfer.execute() action={action!r}")
        try:
            result = bpy.ops.object.mw_copy_skin_weight_maya()
            status = "FINISHED" if 'FINISHED' in result else "CANCELLED"
            core_facade.debug_log("feature_domains", f"weight_transfer.execute() action={action!r} status={status}")
            return {"status": status}
        except Exception as e:
            core_facade.debug_log("feature_domains", f"weight_transfer.execute() action={action!r} raised {e!r}")
            return {"status": "CANCELLED", "message": str(e)}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """UI moved to features/object_tools_ui/ui_weight_transfer.py — see
        docs/domains/skin_tools_ui.md."""
        pass

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        prefs = bpy.context.window_manager.superskin_weight_transfer_prefs
        prefs.keep_old_layer_data = data.get("keep_old_layer_data", False)
        prefs.transfer_method = data.get("transfer_method", "CLOSEST_DISTANCE")

    def serialize_into(self, full_dict: dict) -> None:
        prefs = bpy.context.window_manager.superskin_weight_transfer_prefs
        full_dict["weight_transfer"] = {
            "keep_old_layer_data": prefs.keep_old_layer_data,
            "transfer_method": prefs.transfer_method,
        }


class WeightExportFeature(UnifiedFeatureExtension):
    """Unified extension for the Weight Export dropdown entry."""

    domain_id = "weight_export"
    actions = []
    section_title = "Weight Export"
    draw_tab = "LAYER"
    link = "https://docs.superskinpro.com/transfer_weight/"
    locked_expanded = True

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        # No actions registered — Export runs via its own self-contained operator.
        return {"status": "CANCELLED"}

    def draw_section(self, layout, context) -> None:
        """UI moved to features/object_tools_ui/ui_weight_transfer.py."""
        pass


class WeightImportFeature(UnifiedFeatureExtension):
    """Unified extension for the Weight Import dropdown entry."""

    domain_id = "weight_import"
    actions = []
    section_title = "Weight Import"
    draw_tab = "LAYER"
    link = "https://docs.superskinpro.com/transfer_weight/"
    locked_expanded = True

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        # No actions registered — Import runs via its own self-contained operator.
        return {"status": "CANCELLED"}

    def draw_section(self, layout, context) -> None:
        """UI moved to features/object_tools_ui/ui_weight_transfer.py."""
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register PropertyGroups (WindowManager prefs + Scene work-state) and the
    extension with UnifiedRegistry."""
    bpy.utils.register_class(SSPrefWeightTransfer)
    bpy.types.WindowManager.superskin_weight_transfer_prefs = bpy.props.PointerProperty(
        type=SSPrefWeightTransfer, options={'SKIP_SAVE'},
    )

    bpy.utils.register_class(SSWeightTransferEntryItem)
    bpy.utils.register_class(SSWeightTransferState)
    bpy.types.Scene.superskin_weight_transfer_state = bpy.props.PointerProperty(
        type=SSWeightTransferState,
    )

    UnifiedRegistry.register(WeightTransferFeature())
    UnifiedRegistry.register(WeightExportFeature())
    UnifiedRegistry.register(WeightImportFeature())


def unregister():
    """Unregister the extensions and PropertyGroups."""
    UnifiedRegistry.unregister("weight_import")
    UnifiedRegistry.unregister("weight_export")
    UnifiedRegistry.unregister("weight_transfer")

    try:
        del bpy.types.Scene.superskin_weight_transfer_state
    except Exception:
        pass
    bpy.utils.unregister_class(SSWeightTransferState)
    bpy.utils.unregister_class(SSWeightTransferEntryItem)

    try:
        del bpy.types.WindowManager.superskin_weight_transfer_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefWeightTransfer)
