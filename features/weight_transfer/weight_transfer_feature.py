
import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import state_ops

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")



def _on_changed(self, context):
    from ...core.facade import CoreFacade
    CoreFacade.save_prefs()


def _poll_mesh_object(self, obj):
    return obj.type == 'MESH'


def _on_active_entry_changed(self, context):
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
        description="Requires vertex selection in Edit Mode",
        default=False,
    )
    is_source: bpy.props.BoolProperty(
        name="Is Source",
        description="Use this mesh as a source. Several sources are combined into one",
        default=False,
    )


class SSWeightTransferState(bpy.types.PropertyGroup):
    """Live/working Source + Target configuration for object.mw_copy_skin_weight_transfer,
    registered on bpy.types.Scene."""
    entries: bpy.props.CollectionProperty(type=SSWeightTransferEntryItem)
    active_entry_index: bpy.props.IntProperty(default=0, update=_on_active_entry_changed)


class SSPrefWeightTransfer(bpy.types.PropertyGroup):
    """Weight Transfer settings (per-machine."""
    keep_old_layer_data: bpy.props.BoolProperty(
        name="Keep Old Layer Data",
        description="Keep the target's existing weights and add the transferred ones on top",
        default=False,
        update=_on_changed,
    )
    transfer_method: bpy.props.EnumProperty(
        name="Method",
        items=[
            ('CLOSEST_DISTANCE', "Closest Distance", "Match weights by position"),
            ('VERTEX_ID', "Vertex ID", "Match weights by vertex order (both meshes need the same vertex count)"),
        ],
        default='CLOSEST_DISTANCE',
        update=_on_changed,
    )



class WeightTransferFeature(UnifiedFeatureExtension):
    """Unified extension for the Weight Transfer domain (the live mesh-to-mesh transfer, via
    the unified Source/Target list."""


    domain_id = "weight_transfer"
    actions = ["transfer_weight"]
    section_title = "Weight Transfer"
    draw_tab = "LAYER"
    link = "https://tonmaiart.github.io/superskinpro-docs/object_tools/#weight-transfer"
    defaults_path = _DEFAULTS_PATH
    locked_expanded = True


    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        core_facade.debug_log("feature_domains", f"weight_transfer.execute() action={action!r}")
        try:
            result = bpy.ops.object.mw_copy_skin_weight_transfer()
            status = "FINISHED" if 'FINISHED' in result else "CANCELLED"
            core_facade.debug_log("feature_domains", f"weight_transfer.execute() action={action!r} status={status}")
            return {"status": status}
        except Exception as e:
            core_facade.debug_log("feature_domains", f"weight_transfer.execute() action={action!r} raised {e!r}")
            return {"status": "CANCELLED", "message": str(e)}


    def draw_section(self, layout, context) -> None:
        pass


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
    link = "https://tonmaiart.github.io/superskinpro-docs/object_tools/#weight-transfer"
    locked_expanded = True

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    def draw_section(self, layout, context) -> None:
        pass


class WeightImportFeature(UnifiedFeatureExtension):
    """Unified extension for the Weight Import dropdown entry."""

    domain_id = "weight_import"
    actions = []
    section_title = "Weight Import"
    draw_tab = "LAYER"
    link = "https://tonmaiart.github.io/superskinpro-docs/object_tools/#weight-transfer"
    locked_expanded = True

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    def draw_section(self, layout, context) -> None:
        pass



def register():
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
